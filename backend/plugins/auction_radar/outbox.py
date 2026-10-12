"""28 单进程 Outbox：幂等、dispatcher、SQL UPDATE 状态、SENDING 恢复。"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import text as sql_text

from backend.db.database import engine
from backend.plugins.common import db_append, db_query
from .config import BEIJING_TZ, CONFIG
from .push import push_email

logger = logging.getLogger(__name__)


def now_iso():
    return datetime.now(BEIJING_TZ).isoformat()


def enqueue(event_key, run_id, channel, recipient_key, payload):
    """入队为 WAITING_PUBLISH；快照可见后由 promote_event_to_ready 转 READY。"""
    rows = [{
        "event_key": event_key, "run_id": run_id, "channel": channel,
        "recipient_key": recipient_key,
        "status": "WAITING_PUBLISH",
        "attempts": 0, "next_attempt_at": now_iso(),
        "payload_json": json.dumps(payload, ensure_ascii=False),
        "created_at": now_iso(), "updated_at": now_iso(),
    }]
    return db_append("auction_outbox", rows)


def promote_event_to_ready(event_key):
    """快照发布后把该事件的 WAITING_PUBLISH 任务转 READY（SQL UPDATE）。"""
    df = db_query(
        "SELECT id FROM auction_outbox WHERE event_key=:e AND status='WAITING_PUBLISH'",
        {"e": event_key},
    )
    for r in df.itertuples():
        update_outbox(int(r.id), status="READY", next_attempt_at=now_iso())
    return len(df) if not df.empty else 0


def update_outbox(task_id, **fields):
    """SQL UPDATE（非删除+插入）。"""
    if not fields:
        return 0
    fields["updated_at"] = now_iso()
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    params = dict(fields)
    params["id"] = int(task_id)
    try:
        with engine.begin() as conn:
            return conn.execute(sql_text(f"UPDATE auction_outbox SET {sets} WHERE id = :id"), params).rowcount
    except Exception as exc:  # noqa: BLE001
        logger.warning("update_outbox 失败 id=%s err=%s", task_id, exc)
        return 0


def claim_ready(limit=10):
    df = db_query(
        "SELECT * FROM auction_outbox WHERE status='READY' "
        "AND (next_attempt_at IS NULL OR next_attempt_at <= :n) ORDER BY id LIMIT :l",
        {"n": now_iso(), "l": int(limit)},
    )
    return df.to_dict("records") if not df.empty else []


def mark_sending(task_id):
    return update_outbox(task_id, status="SENDING", sending_started_at=now_iso(), last_attempt_at=now_iso())


def mark_accepted(task_id):
    return update_outbox(task_id, status="ACCEPTED", accepted_at=now_iso())


def mark_retry(task_id, error, attempts):
    delay = min(2 ** max(0, attempts - 1) * 5, 60)
    return update_outbox(task_id, status="RETRY_WAIT", attempts=attempts,
                         last_error=str(error)[:500],
                         next_attempt_at=(datetime.now(BEIJING_TZ) + timedelta(seconds=delay)).isoformat())


def mark_failed_final(task_id, error):
    return update_outbox(task_id, status="FAILED_FINAL", last_error=str(error)[:500])


def _retry_ready(tasks):
    """RETRY_WAIT 到期 -> READY。"""
    n = 0
    for t in tasks:
        if t.get("status") == "RETRY_WAIT" and t.get("next_attempt_at") and t["next_attempt_at"] <= now_iso():
            update_outbox(t["id"], status="READY")
            n += 1
    return n


def dispatch_outbox(limit=10, timeout=15):
    """单 dispatcher 顺序消费 READY，逐通道发送；返回受理结果。"""
    _retry_ready(db_query(
        "SELECT id,status,next_attempt_at FROM auction_outbox WHERE status='RETRY_WAIT'").to_dict("records"))
    tasks = claim_ready(limit)
    accepted, failed = 0, 0
    for t in tasks:
        try:
            payload = json.loads(t.get("payload_json") or "{}")
        except json.JSONDecodeError:
            mark_failed_final(t["id"], "payload_json 非法")
            failed += 1
            continue
        attempts = int(t.get("attempts") or 0)
        if attempts >= int(CONFIG.get("outbox_retry_max", 2)):
            mark_failed_final(t["id"], "retry_exhausted")
            failed += 1
            continue
        mark_sending(t["id"])
        ok, err = push_email(payload.get("subject", ""), payload.get("text", ""),
                             payload.get("html", ""), timeout=timeout)
        if ok:
            mark_accepted(t["id"])
            try:
                from .service import persist_milestone
                persist_milestone(t.get("run_id"), "M4", "done", {"smtp_accepted_at": now_iso()})
            except Exception:  # noqa: BLE001
                logger.warning("M4 里程碑更新失败 event_key=%s", t.get("event_key"))
            accepted += 1
        else:
            mark_retry(t["id"], err, attempts + 1)
            failed += 1
    return {"dispatched": len(tasks), "accepted": accepted, "failed": failed}


def recover_sending(timeout_s=None):
    """启动时把滞留 SENDING 且超时任务重置为 READY。"""
    timeout = timeout_s or int(CONFIG.get("outbox_sending_timeout_s", 30))
    cutoff = (datetime.now(BEIJING_TZ) - timedelta(seconds=timeout)).isoformat()
    df = db_query(
        "SELECT id FROM auction_outbox WHERE status='SENDING' AND sending_started_at < :c",
        {"c": cutoff},
    )
    for r in df.itertuples():
        update_outbox(int(r.id), status="READY", last_error="recovered_after_crash")
    return len(df) if not df.empty else 0
