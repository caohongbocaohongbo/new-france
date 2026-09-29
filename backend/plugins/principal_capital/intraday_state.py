"""23 v2 日内状态模型与纯函数（无网络 / 无文件 I/O 的核心部分）。

状态文件: data/principal_capital_intraday_state.json（唯一临时文件 + os.replace 原子写）。
文件按交易日重置，只保留当前交易日的候选、池、同源累计序列、摘要标记与审计游标。

本模块拆成两层：
  - 纯函数（可独立单测）：merge_candidate_state / append_fund_observation /
    compute_intraday_features / select_radar_pool / should_run_sentinel /
    should_finalize_session / acquire_owner / reset_state_for_trade_date。
  - 薄 I/O 层：load_state / save_state。
"""
import fcntl
import json
import math
import os
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple

from .config import CONFIG, INTRADAY_STATE_FILE

BEIJING_TZ = timezone(timedelta(hours=8))
SCHEMA_VERSION = 2

_DIRECTION_BUY = "buy"
_DIRECTION_SELL = "sell"


class TradingSession(str, Enum):
    """A 股交易时段枚举（值保留小写 am/pm，与既有 JSON/前端/CLI 兼容）。"""

    AM = "am"
    PM = "pm"


class SummaryStatus:
    """Summary Job 状态机（§8）。

    终态：COMPLETED（成功）/ SKIPPED（合法跳过）/ DEAD（重试耗尽，需人工）。
    非终态：NOT_ATTEMPTED / QUEUED / RUNNING / RETRY_WAIT（失败待重试）。
    """

    NOT_ATTEMPTED = "not_attempted"
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"   # 失败，但 attempt_count < max_attempts，等 next_retry_at 后重试
    COMPLETED = "completed"
    SKIPPED = "skipped"         # 合法跳过（终态）：非交易日 / session 不匹配 / 不满足通知门禁
    DEAD = "dead"               # 重试耗尽（终态），需人工 repair
    DELIVERY_UNKNOWN = "delivery_unknown"  # 投递结果不明（终态，禁止自动重发），需人工确认

    # 兼容旧字段名（已用 RETRY_WAIT 取代；保留避免历史状态文件被判非法）
    FAILED = RETRY_WAIT


class SummaryReason:
    """Eligibility / skip / error reason code（§23 / 结构化日志）。"""

    READY = "READY"
    READY_PROVISIONAL = "READY_PROVISIONAL"
    SNAPSHOT_NOT_READY = "SNAPSHOT_NOT_READY"
    SESSION_MISMATCH = "SESSION_MISMATCH"
    NOT_DUE = "NOT_DUE"
    NON_TRADING_DAY = "NON_TRADING_DAY"
    ALREADY_COMPLETED = "ALREADY_COMPLETED"
    ALREADY_RUNNING = "ALREADY_RUNNING"
    NO_VALID_SNAPSHOT = "NO_VALID_SNAPSHOT"
    MAX_RETRIES_EXCEEDED = "MAX_RETRIES_EXCEEDED"
    NOTIFY_NOT_ELIGIBLE = "NOTIFY_NOT_ELIGIBLE"
    WORKER_STALE = "WORKER_STALE"
    SEND_FAILED = "SEND_FAILED"
    DELIVERY_UNKNOWN = "DELIVERY_UNKNOWN"

    # 终态 skip 原因：一旦落到 skipped 就不再重试（产品/语义上不可逆）
    TERMINAL_SKIP_REASONS = frozenset({
        NON_TRADING_DAY, SESSION_MISMATCH, NOTIFY_NOT_ELIGIBLE, ALREADY_COMPLETED,
    })
    # 可恢复原因：落到 RETRY_WAIT，等 next_retry_at 后由 reconciler 重试
    # 注意：DELIVERY_UNKNOWN 不在此列——投递结果不明时禁止自动重发（防重复邮件）。
    RETRYABLE_REASONS = frozenset({
        SNAPSHOT_NOT_READY, NO_VALID_SNAPSHOT, WORKER_STALE, SEND_FAILED,
    })


def is_terminal_skip(reason: Optional[str]) -> bool:
    """该 reason 是否应作为终态 skipped（不可重试）。"""
    return reason in SummaryReason.TERMINAL_SKIP_REASONS


def is_retryable(reason: Optional[str]) -> bool:
    """该 reason 是否应进入 RETRY_WAIT（可被 reconciler 重试）。"""
    return reason in SummaryReason.RETRYABLE_REASONS

# 候选最新指标中保留的字段（稳定、可 JSON 序列化）。
_METRIC_KEYS = (
    "name", "price", "change_pct", "total_amount", "main_net_inflow",
    "main_inflow_ratio", "super_net", "big_net", "mid_net", "small_net", "source",
)


def _json_safe(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _finite(value):
    if value is None or value == "" or value == "-":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _ensure_aware(value: datetime) -> datetime:
    """v2 契约：naive datetime 直接抛 ValueError，不得自动补时区掩盖调用错误。"""
    if getattr(value, "tzinfo", None) is None:
        raise ValueError("datetime 必须带时区（请显式传北京时区）")
    return value


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return _ensure_aware(value)
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return _ensure_aware(parsed)


def _to_iso(value) -> str:
    if isinstance(value, datetime):
        return _ensure_aware(value).isoformat()
    return str(value)


def normalize_code(value) -> str:
    code = str(value or "").strip().zfill(6)
    return code if code.isdigit() and len(code) == 6 else ""


def empty_summary_job(session: str, trade_date: str, max_attempts: Optional[int] = None) -> dict:
    """返回一个完整 Summary Job 状态骨架（§8，UNIQUE(trade_date, session) 的 session 维度）。"""
    return {
        "status": SummaryStatus.NOT_ATTEMPTED,
        "job_id": None,
        "trade_date": trade_date,
        "session": session,
        "selected_snapshot_id": None,
        "finalizer_due_at": None,
        "queued_at": None,
        "started_at": None,
        "completed_at": None,
        "attempt_count": 0,        # 实际发送尝试次数（P1：NO_VALID_SNAPSHOT 等前置失败不消耗）
        "dispatch_count": 0,       # 总 dispatch 次数（诊断用，含前置重试）
        "max_attempts": int(max_attempts) if max_attempts is not None else int(CONFIG.get("summary_max_attempts", 3)),
        "next_retry_at": None,
        "last_error_code": None,
        "last_error_message": None,
        "last_error_at": None,
        "skip_reason": None,
        "trigger_source": None,
        "worker_id": None,
        "run_id": None,          # lease token：防止 stale worker 回魂写状态（P0）
        "history": [],           # 状态迁移历史（最近 N 条）
        "delivery": {            # 邮件投递状态（send 前后持久化，防重复邮件）
            "status": "not_started",  # not_started | sending | accepted | failed | unknown
            "idempotency_key": None,
            "attempt_id": None,
            "sent_at": None,
            "provider": "smtp",
            "message_id": None,
            "last_error": None,
        },
    }


def empty_state(
    trade_date: str,
    owner_id: Optional[str] = None,
    owner_lease_expires_at: Optional[str] = None,
    pipeline_mode: str = "strict",
) -> dict:
    """返回全新日内状态骨架。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "trade_date": trade_date,
        "owner_id": owner_id,
        "owner_lease_expires_at": owner_lease_expires_at,
        "last_batch_id": None,
        "pipeline_mode": pipeline_mode,
        "auto_fallback": None,
        "summary_state": {
            TradingSession.AM.value: empty_summary_job(TradingSession.AM.value, trade_date),
            TradingSession.PM.value: empty_summary_job(TradingSession.PM.value, trade_date),
        },
        "summary_attempted_at": {},
        "summary_sent_at": {},
        "audit_cursor": 0,
        "sentinel_done": [],
        "candidates": {},
        "pool_entries": {},
        "fund_series": {},
        "session_snapshots": {},
    }


def reset_state_for_trade_date(state: Optional[dict], trade_date: str) -> dict:
    """跨交易日清空候选、序列、摘要标记和审计游标。输入不原地修改。"""
    if not isinstance(state, dict) or state.get("trade_date") != trade_date:
        return empty_state(
            trade_date,
            pipeline_mode=(state or {}).get("pipeline_mode", "strict"),
        )
    result = dict(state)
    result.setdefault("schema_version", SCHEMA_VERSION)
    if not isinstance(result.get("summary_state"), dict) or set(result["summary_state"]) != {"am", "pm"}:
        result["summary_state"] = {
            TradingSession.AM.value: empty_summary_job(TradingSession.AM.value, trade_date),
            TradingSession.PM.value: empty_summary_job(TradingSession.PM.value, trade_date),
        }
    result.setdefault("summary_attempted_at", {})
    result.setdefault("summary_sent_at", {})
    result.setdefault("audit_cursor", 0)
    result.setdefault("sentinel_done", [])
    result.setdefault("candidates", {})
    result.setdefault("pool_entries", {})
    result.setdefault("fund_series", {})
    result.setdefault("session_snapshots", {})
    result.setdefault("pipeline_mode", state.get("pipeline_mode", "strict"))
    result.setdefault("auto_fallback", None)
    return result


# --------------------------------------------------------------------------- #
# 唯一写者
# --------------------------------------------------------------------------- #

def acquire_owner(state: dict, owner_id: str, lease_seconds: int, now: datetime) -> tuple:
    """official 启动时抢占/续租 owner。返回 (ok, new_state, reason)。

    - 无 owner 或同 owner：续租成功。
    - 已有不同 owner 且租约未过期：owner_conflict，拒绝写入。
    """
    now = _ensure_aware(now)
    result = dict(state)
    existing = result.get("owner_id")
    if existing and existing != owner_id:
        expires = _parse_dt(result.get("owner_lease_expires_at"))
        if expires and expires > now:
            return False, result, f"owner_conflict: {existing}"
    result["owner_id"] = owner_id
    result["owner_lease_expires_at"] = (now + timedelta(seconds=int(lease_seconds))).isoformat()
    return True, result, None


def release_owner(state: dict) -> dict:
    """session finalizer 完成后主动释放租约（保留 owner_id 便于审计）。"""
    result = dict(state)
    result["owner_lease_expires_at"] = None
    return result


def acquire_owner_atomic(path, owner_id: str, lease_seconds: int, now: datetime) -> tuple:
    """P0-R4：在文件锁临界区内完成 load → 检查 lease → 写入新 lease。

    返回 (ok, state, reason)。lease 持久化完成后才返回，调用方随后才能开始网络请求。
    """
    now = _ensure_aware(now)
    file_path = _state_path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = file_path.with_suffix(file_path.suffix + ".lock")
    with open(lock_path, "a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            state = load_state(path=file_path, trade_date=now.date().isoformat(), now=now)
            ok, state, reason = acquire_owner(state, owner_id, lease_seconds, now)
            if ok:
                save_state(state, path=file_path)
            return ok, state, reason
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def atomic_dispatch_summary_job(path, session: str, snapshot_id, now: datetime, trigger_source: str,
                                job_id: Optional[str] = None, due_at: Optional[str] = None,
                                force: bool = False) -> Tuple[bool, dict, Optional[str]]:
    """§12 原子抢占：文件锁临界区内 CAS dispatch（not_attempted/retry_wait/dead → queued）。

    返回 (ok, state, reason)。ok=False 表示已被其它 worker 抢占（rowcount==0 语义）。
    """
    now = _ensure_aware(now)
    file_path = _state_path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = file_path.with_suffix(file_path.suffix + ".lock")
    with open(lock_path, "a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            state = load_state(path=file_path, trade_date=now.date().isoformat(), now=now)
            ok, state, reason = dispatch_summary_job(
                state, session, snapshot_id, now, trigger_source,
                job_id=job_id, due_at=due_at, force=force,
            )
            if ok:
                save_state(state, path=file_path)
            return ok, state, reason
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def atomic_start_summary_job(path, session: str, worker_id: str, now: datetime,
                             run_id: Optional[str] = None) -> Tuple[bool, dict, Optional[str]]:
    """§13 Worker 原子启动：文件锁临界区内 CAS start（queued → running）。"""
    now = _ensure_aware(now)
    file_path = _state_path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = file_path.with_suffix(file_path.suffix + ".lock")
    with open(lock_path, "a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            state = load_state(path=file_path, trade_date=now.date().isoformat(), now=now)
            ok, state, reason = start_summary_job(state, session, worker_id, now, run_id=run_id)
            if ok:
                save_state(state, path=file_path)
            return ok, state, reason
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def atomic_recover_stale_running(path, now: datetime,
                                 timeout_seconds: Optional[int] = None) -> Tuple[dict, List[str]]:
    """§18 原子化 stale recovery：在文件锁临界区内 load → recover → save。

    修复「read → modify → write 无锁」导致 completed 被旧 state 覆盖成 retry_wait 的竞态。
    返回 (new_state, recovered_sessions)。
    """
    now = _ensure_aware(now)
    file_path = _state_path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = file_path.with_suffix(file_path.suffix + ".lock")
    with open(lock_path, "a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            state = load_state(path=file_path, trade_date=now.date().isoformat(), now=now)
            new_state, recovered = recover_stale_running(state, now, timeout_seconds)
            if recovered:
                save_state(new_state, path=file_path)
            return new_state, recovered
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def atomic_summary_mutate(path, now: datetime, mutate_fn) -> dict:
    """文件锁临界区内 load → mutate_fn(state) → save。
    mutate_fn(state) 返回 new_state(dict) 或 (new_state, extra) 元组；本函数返回 new_state。
    """
    now = _ensure_aware(now)
    file_path = _state_path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = file_path.with_suffix(file_path.suffix + ".lock")
    with open(lock_path, "a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            state = load_state(path=file_path, trade_date=now.date().isoformat(), now=now)
            result = mutate_fn(state)
            new_state = result[0] if (isinstance(result, tuple) and result) else result
            save_state(new_state, path=file_path)
            return new_state
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def mark_sentinel_done(state: dict, label: str) -> dict:
    result = dict(state)
    done = list(result.get("sentinel_done") or [])
    if label not in done:
        done.append(label)
    result["sentinel_done"] = done
    return result


def _set_summary_state(state: dict, session: str, status: str, at_iso: str = None, attempt_id: str = None) -> dict:
    result = dict(state)
    default = {"am": {"status": "not_attempted"}, "pm": {"status": "not_attempted"}}
    summary_state = {s: dict(v) for s, v in (result.get("summary_state") or default).items()}
    entry = summary_state.setdefault(session, {"status": "pending"})
    entry["status"] = status
    if at_iso is not None:
        entry["updated_at"] = at_iso
    if attempt_id is not None:
        entry["attempt_id"] = attempt_id
    result["summary_state"] = summary_state
    return result


def mark_summary_pending(state: dict, session: str, at_iso: str, attempt_id: str) -> dict:
    """SMTP 前原子落盘 pending + attempt_id（P0-5 投递状态机）。"""
    result = _set_summary_state(state, session, "pending", at_iso, attempt_id)
    attempted = dict(result.get("summary_attempted_at") or {})
    attempted[session] = at_iso
    result["summary_attempted_at"] = attempted
    return result


def mark_summary_sent(state: dict, session: str, at_iso: str) -> dict:
    result = _set_summary_state(state, session, "sent", at_iso)
    sent_at = dict(result.get("summary_sent_at") or {})
    sent_at[session] = at_iso
    result["summary_sent_at"] = sent_at
    return result


def mark_summary_explicit_failed(state: dict, session: str, at_iso: str) -> dict:
    return _set_summary_state(state, session, "explicit_failed", at_iso)


def mark_summary_delivery_unknown(state: dict, session: str, at_iso: str) -> dict:
    return _set_summary_state(state, session, "delivery_unknown", at_iso)


# --------------------------------------------------------------------------- #
# Summary Job 状态机（§8/§11/§12/§13/§14/§15/§18）：纯函数 + 原子抢占
# --------------------------------------------------------------------------- #

def _summary_entry(state: dict, session: str) -> dict:
    entry = ((state or {}).get("summary_state") or {}).get(session)
    if not isinstance(entry, dict):
        return empty_summary_job(session, (state or {}).get("trade_date", ""))
    return entry


def _set_summary_job(state: dict, session: str, entry: dict) -> dict:
    result = dict(state)
    summary_state = {s: dict(v) for s, v in (result.get("summary_state") or {}).items()}
    summary_state[session] = entry
    result["summary_state"] = summary_state
    return result


def get_summary_job(state: dict, session: str) -> dict:
    """读取某 session 的 Summary Job（缺失时返回空骨架）。"""
    return _summary_entry(state, session)


def summary_job_idempotency_key(trade_date: str, session: str) -> str:
    """摘要输出幂等键（§9）：summary:YYYY-MM-DD:AM/PM。"""
    return f"summary:{trade_date}:{session.upper()}"


def finalizer_due_at(trade_date: str, session: str, schedule: Optional[dict] = None) -> Optional[datetime]:
    """按 SUMMARY_SCHEDULE 计算 finalizer_due_at（北京时间）。无效时返回 None。"""
    schedule = schedule or CONFIG.get("summary_schedule") or {}
    entry = (schedule or {}).get(session) or {}
    due = entry.get("finalizer_due")
    if not due:
        return None
    try:
        hh, mm = str(due).split(":")
        return datetime(int(trade_date[:4]), int(trade_date[5:7]), int(trade_date[8:10]),
                        int(hh), int(mm), tzinfo=BEIJING_TZ)
    except (TypeError, ValueError, IndexError):
        return None


def _parse_retry_backoff(cfg: Optional[dict] = None) -> List[int]:
    cfg = cfg or {}
    backoff = cfg.get("summary_retry_backoff_seconds") or CONFIG.get("summary_retry_backoff_seconds") or [60, 300, 900]
    return [int(x) for x in backoff]


_HISTORY_MAX = 20


# 状态迁移白名单（P0：终态不可回退；所有迁移必须命中，否则拒绝）
ALLOWED_TRANSITIONS = {
    SummaryStatus.NOT_ATTEMPTED: {SummaryStatus.QUEUED, SummaryStatus.SKIPPED},
    SummaryStatus.QUEUED: {SummaryStatus.RUNNING},
    SummaryStatus.RUNNING: {
        SummaryStatus.COMPLETED, SummaryStatus.RETRY_WAIT, SummaryStatus.DEAD,
        SummaryStatus.SKIPPED, SummaryStatus.DELIVERY_UNKNOWN,
    },
    SummaryStatus.RETRY_WAIT: {SummaryStatus.QUEUED},
    SummaryStatus.DEAD: {SummaryStatus.QUEUED},              # force only（manual repair）
    SummaryStatus.DELIVERY_UNKNOWN: {SummaryStatus.QUEUED},  # force only（manual repair）
    SummaryStatus.COMPLETED: set(),                          # 终态，不可回退
    SummaryStatus.SKIPPED: set(),                            # 终态，不可回退
}


def _record_transition(entry: dict, from_status: Optional[str], to_status: str, now: datetime,
                       reason: Optional[str] = None, worker_id: Optional[str] = None,
                       error: Optional[str] = None) -> dict:
    """在 entry 内追加一条状态迁移历史（保留最近 _HISTORY_MAX 条），返回修改后的 entry。"""
    history = list(entry.get("history") or [])[-(_HISTORY_MAX - 1):]
    history.append({
        "from": from_status,
        "to": to_status,
        "at": now.isoformat(),
        "reason": reason,
        "worker_id": worker_id,
        "error": (str(error)[:200] if error else None),
    })
    entry["history"] = history
    return entry


def _transition_allowed(from_status: Optional[str], to_status: str) -> bool:
    """校验 from → to 是否在白名单内。"""
    return to_status in ALLOWED_TRANSITIONS.get(from_status, set())


def _next_retry_after(attempt_count: int, now: datetime, cfg: Optional[dict] = None) -> str:
    """按 attempt_count 计算下次重试时间（backoff[attempt-1]，越界取末位）。"""
    backoff = _parse_retry_backoff(cfg)
    idx = max(0, min(int(attempt_count) - 1, len(backoff) - 1))
    return (now + timedelta(seconds=backoff[idx])).isoformat()


def dispatch_summary_job(
    state: dict,
    session: str,
    snapshot_id: Optional[str],
    now: datetime,
    trigger_source: str,
    job_id: Optional[str] = None,
    due_at: Optional[str] = None,
    force: bool = False,
) -> Tuple[bool, dict, Optional[str]]:
    """§12 原子抢占的纯函数部分：not_attempted/retry_wait → queued。

    返回 (ok, new_state, reason)。重复 dispatch 时 ok=False（调用方据 rowcount==0 跳过）。
    """
    now = _ensure_aware(now)
    entry = dict(_summary_entry(state, session))
    status = entry.get("status", SummaryStatus.NOT_ATTEMPTED)

    if status in (SummaryStatus.QUEUED, SummaryStatus.RUNNING):
        return False, state, SummaryReason.ALREADY_RUNNING
    if status == SummaryStatus.COMPLETED:
        return False, state, SummaryReason.ALREADY_COMPLETED
    if status == SummaryStatus.SKIPPED:
        return False, state, entry.get("skip_reason") or SummaryReason.NOT_DUE
    if status == SummaryStatus.DELIVERY_UNKNOWN:
        if not force:
            return False, state, SummaryReason.DELIVERY_UNKNOWN  # 终态：投递不明，禁止自动重发
        entry["attempt_count"] = 0  # manual repair：操作者确认后重发
    if status == SummaryStatus.DEAD:
        if not force:
            return False, state, SummaryReason.MAX_RETRIES_EXCEEDED
        entry["attempt_count"] = 0  # manual repair：重置重试计数，重新进入队列
    if status == SummaryStatus.RETRY_WAIT:
        next_retry = _parse_dt(entry.get("next_retry_at"))
        if not force and next_retry is not None and next_retry > now:
            return False, state, SummaryReason.NOT_DUE

    from_status = status
    if not _transition_allowed(from_status, SummaryStatus.QUEUED):
        return False, state, SummaryReason.ALREADY_COMPLETED if from_status == SummaryStatus.COMPLETED else SummaryReason.NOT_DUE
    entry.update({
        "status": SummaryStatus.QUEUED,
        "job_id": job_id or entry.get("job_id") or uuid.uuid4().hex,
        "selected_snapshot_id": snapshot_id or entry.get("selected_snapshot_id"),
        "finalizer_due_at": due_at or entry.get("finalizer_due_at"),
        "queued_at": now.isoformat(),
        "trigger_source": trigger_source,
        "started_at": None,
        "completed_at": None,
        "next_retry_at": None,
        "run_id": None,
    })
    _record_transition(entry, from_status, SummaryStatus.QUEUED, now, reason=trigger_source)
    return True, _set_summary_job(state, session, entry), None


def start_summary_job(state: dict, session: str, worker_id: str, now: datetime,
                      run_id: Optional[str] = None) -> Tuple[bool, dict, Optional[str]]:
    """§13 Worker 原子启动：queued → running，attempt_count+1，写入 run_id（lease token）。"""
    now = _ensure_aware(now)
    entry = dict(_summary_entry(state, session))
    from_status = entry.get("status")
    if from_status != SummaryStatus.QUEUED:
        return False, state, SummaryReason.ALREADY_RUNNING
    token = run_id or uuid.uuid4().hex
    entry.update({
        "status": SummaryStatus.RUNNING,
        "started_at": now.isoformat(),
        "worker_id": worker_id,
        "run_id": token,
        "dispatch_count": int(entry.get("dispatch_count", 0)) + 1,
    })
    _record_transition(entry, from_status, SummaryStatus.RUNNING, now, worker_id=worker_id)
    return True, _set_summary_job(state, session, entry), None


def _run_id_matches(entry: dict, run_id: Optional[str]) -> bool:
    """严格匹配：worker 写操作必须持有当前 run_id（防 stale worker 回魂）。

    - run_id 为空 → 仅当 job 当前无 run_id（非 running/queued 态）时放行（终态迁移，如 not_attempted→skipped）。
    - run_id 非空 → 必须与 entry 当前 run_id 严格相等；entry 无 run_id（已被 recovery 清空）→ 拒绝。
    """
    if run_id is None:
        return not bool(entry.get("run_id"))
    return bool(entry.get("run_id")) and entry.get("run_id") == run_id


def complete_summary_job(state: dict, session: str, now: datetime, run_id: Optional[str] = None) -> dict:
    """§14 成功提交：running → completed。run_id 不匹配 / 非法 from 状态则拒绝写入（返回原 state）。"""
    now = _ensure_aware(now)
    entry = dict(_summary_entry(state, session))
    if not _run_id_matches(entry, run_id):
        return state
    if not _transition_allowed(entry.get("status"), SummaryStatus.COMPLETED):
        return state
    from_status = entry.get("status")
    entry.update({
        "status": SummaryStatus.COMPLETED,
        "completed_at": now.isoformat(),
        "last_error_code": None,
        "last_error_message": None,
    })
    _record_transition(entry, from_status, SummaryStatus.COMPLETED, now, worker_id=entry.get("worker_id"))
    return _set_summary_job(state, session, entry)


def fail_summary_job(state: dict, session: str, error_code: str, message: str,
                     now: datetime, next_retry_at: Optional[str] = None,
                     run_id: Optional[str] = None) -> dict:
    """§15 失败处理：running → retry_wait / dead（按 attempt_count 与 max_attempts）。

    - attempt_count < max_attempts → RETRY_WAIT（等 next_retry_at 重试）。
    - attempt_count >= max_attempts → DEAD（终态，需人工 repair）。
    """
    now = _ensure_aware(now)
    entry = dict(_summary_entry(state, session))
    if not _run_id_matches(entry, run_id):
        return state
    if not _transition_allowed(entry.get("status"), SummaryStatus.RETRY_WAIT) and \
       not _transition_allowed(entry.get("status"), SummaryStatus.DEAD):
        return state
    from_status = entry.get("status")
    attempts = int(entry.get("attempt_count", 0))
    max_attempts = int(entry.get("max_attempts", 3))
    dead = attempts >= max_attempts
    target = SummaryStatus.DEAD if dead else SummaryStatus.RETRY_WAIT
    entry.update({
        "status": target,
        "last_error_code": error_code,
        "last_error_message": message[:500] if message else None,
        "last_error_at": now.isoformat(),
        "next_retry_at": None if dead else (next_retry_at or _next_retry_after(attempts, now)),
    })
    _record_transition(entry, from_status, target, now, reason=error_code,
                       worker_id=entry.get("worker_id"), error=message)
    return _set_summary_job(state, session, entry)


def skip_summary_job(state: dict, session: str, reason: str, now: datetime,
                     run_id: Optional[str] = None) -> dict:
    """合法跳过（终态）：非交易日 / session 不匹配 / 不满足通知门禁 → skipped。"""
    now = _ensure_aware(now)
    entry = dict(_summary_entry(state, session))
    if not _run_id_matches(entry, run_id):
        return state
    if not _transition_allowed(entry.get("status"), SummaryStatus.SKIPPED):
        return state
    from_status = entry.get("status")
    entry.update({
        "status": SummaryStatus.SKIPPED,
        "skip_reason": reason,
        "completed_at": entry.get("completed_at") or now.isoformat(),
    })
    _record_transition(entry, from_status, SummaryStatus.SKIPPED, now, reason=reason,
                       worker_id=entry.get("worker_id"))
    return _set_summary_job(state, session, entry)


def _set_delivery(state: dict, session: str, updates: dict, run_id: Optional[str] = None) -> dict:
    """更新 delivery 子状态（run_id 不匹配则拒绝）。"""
    entry = dict(_summary_entry(state, session))
    if not _run_id_matches(entry, run_id):
        return state
    delivery = dict(entry.get("delivery") or {})
    delivery.update(updates)
    entry["delivery"] = delivery
    return _set_summary_job(state, session, entry)


def mark_delivery_sending(state: dict, session: str, idempotency_key: str, attempt_id: str,
                          now: datetime, run_id: Optional[str] = None) -> dict:
    """send_email 前持久化「sending」标记（崩溃窗口防重复邮件）并计入实际发送尝试。"""
    entry = dict(_summary_entry(state, session))
    if not _run_id_matches(entry, run_id):
        return state
    delivery = dict(entry.get("delivery") or {})
    delivery.update({
        "status": "sending",
        "idempotency_key": idempotency_key,
        "attempt_id": attempt_id,
        "sent_at": None,
        "last_error": None,
    })
    entry["delivery"] = delivery
    # P1：只有真正进入发送阶段才消耗 attempt_count（NO_VALID_SNAPSHOT 前置失败不消耗）
    entry["attempt_count"] = int(entry.get("attempt_count", 0)) + 1
    return _set_summary_job(state, session, entry)


def mark_delivery_accepted(state: dict, session: str, now: datetime,
                           run_id: Optional[str] = None, message_id: Optional[str] = None) -> dict:
    return _set_delivery(state, session, {
        "status": "accepted",
        "sent_at": now.isoformat(),
        "message_id": message_id,
        "last_error": None,
    }, run_id=run_id)


def mark_delivery_failed(state: dict, session: str, error: str, now: datetime,
                         run_id: Optional[str] = None) -> dict:
    return _set_delivery(state, session, {
        "status": "failed",
        "last_error": (str(error)[:200] if error else None),
    }, run_id=run_id)


def mark_delivery_unknown_terminal(state: dict, session: str, error: str, now: datetime,
                                   run_id: Optional[str] = None) -> dict:
    """投递结果不明 → 终态 delivery_unknown（禁止自动重发）。"""
    now = _ensure_aware(now)
    entry = dict(_summary_entry(state, session))
    if not _run_id_matches(entry, run_id):
        return state
    if not _transition_allowed(entry.get("status"), SummaryStatus.DELIVERY_UNKNOWN):
        return state
    from_status = entry.get("status")
    delivery = dict(entry.get("delivery") or {})
    delivery.update({"status": "unknown", "last_error": (str(error)[:200] if error else None)})
    entry["delivery"] = delivery
    entry["status"] = SummaryStatus.DELIVERY_UNKNOWN
    entry["last_error_code"] = SummaryReason.DELIVERY_UNKNOWN
    entry["last_error_message"] = (str(error)[:500] if error else None)
    entry["last_error_at"] = now.isoformat()
    _record_transition(entry, from_status, SummaryStatus.DELIVERY_UNKNOWN, now,
                       reason=SummaryReason.DELIVERY_UNKNOWN,
                       worker_id=entry.get("worker_id"), error=error)
    return _set_summary_job(state, session, entry)


def recover_stale_running(state: dict, now: datetime, timeout_seconds: Optional[int] = None) -> Tuple[dict, List[str]]:
    """§18 Stale Running Recovery：running 且超时 → retry_wait/dead（WORKER_STALE）待重试。"""
    now = _ensure_aware(now)
    timeout_seconds = int(timeout_seconds if timeout_seconds is not None
                          else CONFIG.get("summary_running_timeout_seconds", 600))
    recovered: List[str] = []
    result = state
    for session in (TradingSession.AM.value, TradingSession.PM.value):
        entry = dict(_summary_entry(result, session))
        if entry.get("status") != SummaryStatus.RUNNING:
            continue
        started = _parse_dt(entry.get("started_at"))
        if started is not None and (now - started).total_seconds() <= timeout_seconds:
            continue
        delivery_status = (entry.get("delivery") or {}).get("status")
        from_status = entry.get("status")  # "running"
        if delivery_status == "sending":
            # 崩溃窗口：send_email 前已落盘「sending」，send 结果不明 → 终态 delivery_unknown，禁止自动重发
            delivery = dict(entry.get("delivery") or {})
            delivery.update({"status": "unknown", "last_error": "worker 在 sending 阶段卡死，投递结果不明"})
            entry["delivery"] = delivery
            entry["status"] = SummaryStatus.DELIVERY_UNKNOWN
            entry["last_error_code"] = SummaryReason.DELIVERY_UNKNOWN
            entry["last_error_message"] = "running 期间 delivery=sending 卡死，投递结果不明"
            entry["last_error_at"] = now.isoformat()
            entry["next_retry_at"] = None
            entry["run_id"] = None
            _record_transition(entry, from_status, SummaryStatus.DELIVERY_UNKNOWN, now,
                               reason=SummaryReason.DELIVERY_UNKNOWN,
                               worker_id=entry.get("worker_id"))
            result = _set_summary_job(result, session, entry)
            recovered.append(session)
            continue
        attempts = int(entry.get("attempt_count", 0))
        dead = attempts >= int(entry.get("max_attempts", 3))
        target = SummaryStatus.DEAD if dead else SummaryStatus.RETRY_WAIT
        entry.update({
            "status": target,
            "last_error_code": SummaryReason.WORKER_STALE,
            "last_error_message": f"running 超过 {timeout_seconds}s 未完成，判定 worker 卡死",
            "last_error_at": now.isoformat(),
            "next_retry_at": None if dead else _next_retry_after(attempts, now),
            "run_id": None,
        })
        _record_transition(entry, from_status, target, now, reason=SummaryReason.WORKER_STALE,
                           worker_id=entry.get("worker_id"))
        result = _set_summary_job(result, session, entry)
        recovered.append(session)
    return result, recovered


def summary_jobs_due(state: dict, now: datetime, schedule: Optional[dict] = None) -> List[dict]:
    """§16 Reconciler 扫描：返回 (session, reason) 需要补调度的 Job 列表。

    条件：finalizer_due_at <= now 且 status 为 not_attempted，或 retry_wait 且 next_retry_at <= now。
    """
    now = _ensure_aware(now)
    due: List[dict] = []
    for session in (TradingSession.AM.value, TradingSession.PM.value):
        entry = _summary_entry(state, session)
        status = entry.get("status", SummaryStatus.NOT_ATTEMPTED)
        if status == SummaryStatus.NOT_ATTEMPTED:
            due_at = finalizer_due_at(entry.get("trade_date") or now.date().isoformat(), session, schedule)
            if due_at is not None and due_at <= now:
                due.append({"session": session, "reason": SummaryReason.NOT_DUE, "snapshot_id": None, "due_at": due_at.isoformat()})
        elif status == SummaryStatus.RETRY_WAIT:
            next_retry = _parse_dt(entry.get("next_retry_at"))
            if next_retry is None or next_retry <= now:
                due.append({"session": session, "reason": SummaryReason.NOT_DUE, "snapshot_id": entry.get("selected_snapshot_id"), "due_at": entry.get("finalizer_due_at")})
    return due


def session_cutoff(trade_date: str, session: str, schedule: Optional[dict] = None) -> Optional[datetime]:
    """返回该 session 的 cutoff（AM=11:30 / PM=15:00，北京时间）。"""
    schedule = schedule or CONFIG.get("summary_schedule") or {}
    entry = (schedule or {}).get(session) or {}
    cutoff = entry.get("cutoff")
    if not cutoff:
        return None
    try:
        hh, mm = str(cutoff).split(":")
        return datetime(int(trade_date[:4]), int(trade_date[5:7]), int(trade_date[8:10]),
                        int(hh), int(mm), tzinfo=BEIJING_TZ)
    except (TypeError, ValueError, IndexError):
        return None


def summary_eligibility(state: dict, session: str, snapshot: Optional[dict],
                        now: datetime, cfg: Optional[dict] = None) -> dict:
    """Session 感知的摘要 eligibility（§5/§6/§23）。

    返回 {eligible, reason}。reason 为 SummaryReason 枚举值；eligible=False 时必须给出可解释原因。
    """
    now = _ensure_aware(now)
    cfg = cfg or {}
    job = get_summary_job(state, session)
    status = job.get("status", SummaryStatus.NOT_ATTEMPTED)
    if status == SummaryStatus.COMPLETED:
        return {"eligible": False, "reason": SummaryReason.ALREADY_COMPLETED}
    if status == SummaryStatus.SKIPPED:
        return {"eligible": False, "reason": job.get("skip_reason") or SummaryReason.NOT_DUE}
    # 注意：不在此处判定 running/queued —— 运行态由 dispatch/start 的 CAS 与
    # stale running recovery 处理；本函数只回答「快照是否满足摘要 eligibility」。

    if not snapshot or not isinstance(snapshot, dict):
        return {"eligible": False, "reason": SummaryReason.NO_VALID_SNAPSHOT}
    if snapshot.get("session") != session:
        return {"eligible": False, "reason": SummaryReason.SESSION_MISMATCH}
    if snapshot.get("trade_date") != now.date().isoformat():
        return {"eligible": False, "reason": SummaryReason.SESSION_MISMATCH}
    if snapshot.get("status") != "completed":
        return {"eligible": False, "reason": SummaryReason.SNAPSHOT_NOT_READY}

    # 会话相对新鲜度：快照应采集于该 session 交易窗口（距 cutoff 不超过 summary_max_age_min）。
    # 与「now」解耦，保证 reconciler/repair 在盘后补跑 AM 摘要不会被误判 stale。
    cutoff = session_cutoff(now.date().isoformat(), session, cfg.get("summary_schedule"))
    captured = _parse_dt(snapshot.get("captured_at") or snapshot.get("now"))
    max_age_min = int(cfg.get("summary_max_age_min", CONFIG["summary_max_age_min"]))
    if captured is None or cutoff is None or (cutoff - captured).total_seconds() > max_age_min * 60:
        return {"eligible": False, "reason": SummaryReason.SNAPSHOT_NOT_READY}

    quality = snapshot.get("quality") or {}
    quality_status = quality.get("status")
    if quality_status == "accepted":
        return {"eligible": True, "reason": SummaryReason.READY}
    if quality_status == "provisional":
        # 明确语义：provisional（无 source_time）仅在 allow_provisional_notify 打开时才发摘要。
        allow_provisional = bool(cfg.get("allow_provisional_notify", CONFIG["allow_provisional_notify"]))
        if allow_provisional:
            return {"eligible": True, "reason": SummaryReason.READY_PROVISIONAL}
        return {"eligible": False, "reason": SummaryReason.NOTIFY_NOT_ELIGIBLE}
    if quality_status == "degraded":
        return {"eligible": False, "reason": SummaryReason.NOTIFY_NOT_ELIGIBLE}
    # 兼容无 status 字段的旧快照：退回 notify_eligible 布尔
    if quality.get("notify_eligible"):
        return {"eligible": True, "reason": SummaryReason.READY}
    return {"eligible": False, "reason": SummaryReason.NOTIFY_NOT_ELIGIBLE}


def assess_summary_health(state: dict, session: str, snapshot: Optional[dict],
                          now: datetime, cfg: Optional[dict] = None) -> dict:
    """单一健康判定（doctor 与 watchdog 复用的唯一判定来源）。

    返回 {health, repairable, reason, recommended_action}。health ∈ healthy|warning|broken。
    """
    job = get_summary_job(state, session)
    status = job.get("status", SummaryStatus.NOT_ATTEMPTED)
    eligibility = summary_eligibility(state, session, snapshot, now, cfg)
    if status == SummaryStatus.COMPLETED:
        return {"health": "healthy", "repairable": False, "reason": SummaryReason.ALREADY_COMPLETED, "recommended_action": "已生成摘要，无需处理"}
    if status == SummaryStatus.SKIPPED:
        skip_reason = job.get("skip_reason")
        # SESSION_MISMATCH 是数据完整性问题，不能算健康
        if skip_reason == SummaryReason.SESSION_MISMATCH:
            return {"health": "broken", "repairable": True, "reason": SummaryReason.SESSION_MISMATCH,
                    "recommended_action": "快照 session 与目标 session 不一致，检查 session_snapshots 与快照绑定"}
        return {"health": "healthy", "repairable": False, "reason": skip_reason, "recommended_action": "终态跳过，无需处理"}
    if status == SummaryStatus.DEAD:
        return {"health": "broken", "repairable": True, "reason": job.get("last_error_code") or SummaryReason.MAX_RETRIES_EXCEEDED, "recommended_action": "summary_repair --execute 补跑"}
    if status == SummaryStatus.DELIVERY_UNKNOWN:
        return {"health": "broken", "repairable": True, "reason": SummaryReason.DELIVERY_UNKNOWN, "recommended_action": "人工确认投递结果后 summary_repair --execute"}
    if status == SummaryStatus.RETRY_WAIT:
        return {"health": "warning", "repairable": True, "reason": job.get("last_error_code"), "recommended_action": "reconciler 会自动重试"}
    if status == SummaryStatus.QUEUED:
        return {"health": "warning", "repairable": True, "reason": "queued", "recommended_action": "等待 worker 消费"}
    if status == SummaryStatus.RUNNING:
        return {"health": "warning", "repairable": True, "reason": "running", "recommended_action": "等待完成，超时由 reconciler 恢复"}
    # not_attempted：用 finalizer_due_at 判断是否真的 overdue（修「提前告警」）
    due_at = finalizer_due_at(now.date().isoformat(), session, (cfg or {}).get("summary_schedule") if cfg else None)
    if due_at is not None and now < due_at:
        return {"health": "healthy", "repairable": False, "reason": SummaryReason.NOT_DUE, "recommended_action": "未到 finalizer_due_at"}
    return {"health": "warning", "repairable": True, "reason": eligibility["reason"], "recommended_action": "scheduler/reconciler 会处理"}


# --------------------------------------------------------------------------- #
# 纯函数：候选状态合并
# --------------------------------------------------------------------------- #

def _candidate_metrics(row: dict) -> dict:
    return _json_safe({key: row.get(key) for key in _METRIC_KEYS})


def merge_candidate_state(
    previous: Optional[dict],
    current_buy: list,
    current_sell: list,
    batch_meta: dict,
) -> dict:
    """合并本批候选到当日候选状态。返回新的 candidates 字典（不原地修改）。

    - 首次出现的键 fresh=true；已存在 fresh=false。
    - 完整批次未命中的键 is_current=false；partial 批次不据此驱逐。
    - 同 batch_id 重放幂等：不重复增加 seen_rounds。
    """
    previous_candidates = (previous or {}).get("candidates") if isinstance(previous, dict) else (previous or {})
    candidates = {key: dict(entry) for key, entry in (previous_candidates or {}).items()}
    now_iso = _to_iso(batch_meta.get("now"))
    batch_id = str(batch_meta.get("batch_id") or uuid.uuid4().hex)
    is_partial = bool(batch_meta.get("is_partial", False))

    seen = set()
    for direction, rows in ((_DIRECTION_BUY, current_buy), (_DIRECTION_SELL, current_sell)):
        for row in rows or []:
            code = normalize_code(row.get("code"))
            if not code:
                continue
            key = f"{direction}:{code}"
            seen.add(key)
            entry = candidates.get(key)
            if entry is None:
                candidates[key] = {
                    "first_seen_at": now_iso,
                    "last_seen_at": now_iso,
                    "last_batch_id": batch_id,
                    "seen_rounds": 1,
                    "is_current": True,
                    "latest_metrics": _candidate_metrics(row),
                    "quality_status": row.get("quality_status", "provisional"),
                    "fresh": True,
                }
                continue
            # 同 batch 重放幂等
            if entry.get("last_batch_id") == batch_id:
                candidates[key] = entry
                continue
            entry["last_seen_at"] = now_iso
            entry["last_batch_id"] = batch_id
            entry["seen_rounds"] = int(entry.get("seen_rounds", 0)) + 1
            entry["is_current"] = True
            entry["latest_metrics"] = _candidate_metrics(row)
            entry["quality_status"] = row.get("quality_status", entry.get("quality_status", "provisional"))
            entry["fresh"] = False
            candidates[key] = entry

    if not is_partial:
        for key, entry in list(candidates.items()):
            if key not in seen and entry.get("is_current"):
                entry["is_current"] = False
                candidates[key] = entry
    return candidates


def current_candidate_lists(state: Optional[dict]) -> tuple:
    """从日内状态导出报告用的 current/today 列表（稳定排序）。"""
    candidates = (state or {}).get("candidates") or {}
    buy_current, sell_current, buy_today, sell_today = [], [], [], []
    for key, entry in candidates.items():
        if ":" not in key:
            continue
        direction, code = key.split(":", 1)
        metrics = dict(entry.get("latest_metrics") or {})
        row = {"code": code, **metrics}
        if direction == _DIRECTION_BUY:
            buy_today.append(dict(row))
            if entry.get("is_current"):
                buy_current.append(dict(row))
        else:
            sell_today.append(dict(row))
            if entry.get("is_current"):
                sell_current.append(dict(row))
    for lst in (buy_current, sell_current, buy_today, sell_today):
        lst.sort(key=lambda item: str(item.get("code")))
    return buy_current, sell_current, buy_today, sell_today


# --------------------------------------------------------------------------- #
# 纯函数：同源日内资金特征
# --------------------------------------------------------------------------- #

def _obs_eligible(obs: dict) -> bool:
    if obs.get("partial") or obs.get("stale") or obs.get("cache"):
        return False
    return _finite(obs.get("main_net_inflow")) is not None and _finite(obs.get("total_amount")) is not None


def append_fund_observation(series: dict, row: dict, batch_meta: dict, cfg: dict) -> dict:
    """给某 code 追加一条同源累计快照。返回新的 fund_series 字典（不原地修改）。

    按 code + batch_id 幂等：同 batch 重放不重复追加。
    """
    result = {code: list(items) for code, items in (series or {}).items()}
    code = normalize_code(row.get("code"))
    if not code:
        return result
    max_points = int((cfg or {}).get("intraday_max_points", CONFIG["intraday_max_points"]))
    observation = {
        "observed_at": _to_iso(batch_meta.get("observed_at") or batch_meta.get("now")),
        "source_segment": batch_meta.get("source_segment") or f"{row.get('source', 'sina_single')}:v1:{batch_meta.get('trade_date', '')}",
        "main_net_inflow": _finite(row.get("main_net_inflow")),
        "total_amount": _finite(row.get("total_amount")),
        "batch_id": str(batch_meta.get("batch_id") or ""),
        "partial": bool(batch_meta.get("is_partial", False)),
        "stale": bool(batch_meta.get("is_stale", False)),
        "cache": bool(batch_meta.get("is_cache", False)),
    }
    items = result.get(code, [])
    if items and items[-1].get("batch_id") == observation["batch_id"]:
        return result  # 同 batch 幂等
    items.append(observation)
    result[code] = items[-max_points:]
    return result


def _warm(reason: str) -> dict:
    return {
        "roll_net_30m": None,
        "acc_win": 0,
        "inc_ratio_5m": None,
        "interval_seconds": None,
        "warming": True,
        "warming_reason": reason,
    }


def compute_intraday_features(series: list, now: datetime, cfg: dict = None) -> dict:
    """对同一 code 的累计快照序列计算 5 分钟增量 / 30 分钟滚动特征。

    只对同源、同交易日、连续、且 total_amount 不回退的相邻快照差分。
    间隔不在 [2,10] 分钟、切源、累计值回退、跨日均开新段并 warming。
    """
    del cfg
    now = _ensure_aware(now)
    if not series:
        return _warm("first_observation")
    points = []
    for obs in series:
        ts = _parse_dt(obs.get("observed_at"))
        if ts is None or ts.date() != now.date():
            continue
        points.append((ts, obs))
    points.sort(key=lambda item: item[0])
    if len(points) < 2:
        return _warm("first_observation")

    pairs = []
    for index in range(len(points) - 2, -1, -1):
        prev_ts, prev = points[index]
        cur_ts, cur = points[index + 1]
        # invalid/partial/cache/stale 必须中断连续段（不能先过滤后跨越该点配对）
        if not _obs_eligible(prev) or not _obs_eligible(cur):
            break
        if prev.get("source_segment") != cur.get("source_segment"):
            break  # 切源：开新段，之前的历史不再与本段拼接
        delta_seconds = (cur_ts - prev_ts).total_seconds()
        if delta_seconds < 120 or delta_seconds > 600:
            break
        prev_total = _finite(prev.get("total_amount"))
        cur_total = _finite(cur.get("total_amount"))
        if prev_total is None or cur_total is None or cur_total < prev_total:
            break  # 累计值回退：开新段
        pairs.append({
            "delta_main_net": _finite(cur.get("main_net_inflow")) - _finite(prev.get("main_net_inflow")),
            "delta_total_amount": cur_total - prev_total,
            "interval_seconds": delta_seconds,
            "prev_at": prev_ts,
            "at": cur_ts,
        })
    pairs.reverse()
    if not pairs:
        # 无法构成任何有效相邻差分；给出最近一次导致开新段的原因
        prev_ts, prev = points[-2]
        cur_ts, cur = points[-1]
        if not _obs_eligible(prev) or not _obs_eligible(cur):
            return _warm("batch_partial")
        if prev.get("source_segment") != cur.get("source_segment"):
            return _warm("source_changed")
        delta_seconds = (cur_ts - prev_ts).total_seconds()
        if delta_seconds > 600:
            return _warm("gap_too_large")
        if delta_seconds < 120:
            return _warm("interval_too_small")
        if _finite(cur.get("total_amount")) is not None and _finite(prev.get("total_amount")) is not None \
                and _finite(cur.get("total_amount")) < _finite(prev.get("total_amount")):
            return _warm("counter_reset")
        return _warm("first_observation")

    result = {
        "roll_net_30m": None,
        "acc_win": 0,
        "inc_ratio_5m": None,
        "interval_seconds": None,
        "warming": False,
        "warming_reason": None,
    }

    # acc_win：从最新值向前数连续 delta_main_net > 0 的有效窗口数
    acc = 0
    for pair in reversed(pairs):
        if pair["delta_main_net"] > 0:
            acc += 1
        else:
            break
    result["acc_win"] = acc

    latest = pairs[-1]
    if latest["delta_total_amount"] > 0:
        result["inc_ratio_5m"] = round(latest["delta_main_net"] / latest["delta_total_amount"] * 100, 4)
        result["interval_seconds"] = int(latest["interval_seconds"])

    cutoff = now - timedelta(minutes=30)
    recent = [pair for pair in pairs if pair["at"] >= cutoff]
    if not recent:
        result["roll_net_30m"] = None
        result["warming"] = True
        result["warming_reason"] = "insufficient_coverage"
        return result

    recent_times = sorted({pair["prev_at"] for pair in recent} | {recent[-1]["at"]})
    span_seconds = (recent_times[-1] - recent_times[0]).total_seconds()
    max_gap = max(
        (recent_times[i + 1] - recent_times[i]).total_seconds()
        for i in range(len(recent_times) - 1)
    ) if len(recent_times) > 1 else 0.0
    if span_seconds < 25 * 60 or max_gap > 600:
        result["roll_net_30m"] = None
        result["warming"] = True
        result["warming_reason"] = "insufficient_coverage"
        return result

    result["roll_net_30m"] = round(sum(pair["delta_main_net"] for pair in recent), 2)
    return result


# --------------------------------------------------------------------------- #
# 纯函数：雷达池选择
# --------------------------------------------------------------------------- #

def _pool_score(entry: dict) -> tuple:
    metrics = entry.get("latest_metrics") or {}
    ratio = _finite(metrics.get("main_inflow_ratio")) or 0.0
    net = _finite(metrics.get("main_net_inflow")) or 0.0
    return (-ratio, -net, str(entry.get("code") or ""))


def _fresh_score(row: dict) -> tuple:
    ratio = _finite(row.get("main_inflow_ratio")) or 0.0
    net = _finite(row.get("main_net_inflow")) or 0.0
    return (-ratio, -net, str(normalize_code(row.get("code")) or ""))


def _pool_stale(entry: dict, now: datetime, max_stale_min: int) -> bool:
    last_seen = _parse_dt(entry.get("last_seen_at"))
    if last_seen is None:
        return True
    return (now - last_seen).total_seconds() > int(max_stale_min) * 60


def _pool_in_dwell(entry: dict, now: datetime, min_dwell_min: int) -> bool:
    dwell_until = _parse_dt(entry.get("dwell_until"))
    return dwell_until is not None and dwell_until > now


def select_radar_pool(previous_pool: Optional[dict], current_buy: list, now: datetime, cfg: dict) -> dict:
    """从本轮完整 buy_candidates_current 选择吸筹观察池。

    - current_buy=None 表示 partial 批次：不驱逐旧成员，只移除超过最大陈旧期限者。
    - 完整批次：保留驻留期内的旧成员（上限 protected_cap，且必须给轮换席让路），
      剩余席位按 fresh 候选稳定排序填入。
    """
    now = _ensure_aware(now)
    cfg = cfg or {}
    previous = previous_pool or {}
    max_n = int(cfg.get("radar_pool_max", 40))
    min_dwell = int(cfg.get("radar_pool_min_dwell_min", 30))
    protected_cap = int(cfg.get("radar_pool_protected_cap", 30))
    rotation = int(cfg.get("radar_pool_rotation_seats", 10))
    max_stale = int(cfg.get("radar_pool_max_stale_min", 15))

    if current_buy is None:
        return {
            code: dict(entry)
            for code, entry in previous.items()
            if not _pool_stale(entry, now, max_stale)
        }

    buy_map = {normalize_code(row.get("code")): row for row in current_buy if normalize_code(row.get("code"))}
    protected = []
    for code, entry in previous.items():
        if not _pool_stale(entry, now, max_stale) and _pool_in_dwell(entry, now, min_dwell):
            protected.append((code, entry))
    protected.sort(key=lambda item: _pool_score(item[1]))

    keep_protected = min(len(protected), protected_cap, max(0, max_n - rotation))
    kept = protected[:keep_protected]
    kept_codes = {code for code, _ in kept}

    fresh = []
    for code, row in buy_map.items():
        if code in kept_codes:
            continue
        fresh.append((code, row))
    fresh.sort(key=lambda item: _fresh_score(item[1]))

    remaining = max_n - len(kept)
    result = {}
    for code, entry in kept:
        item = dict(entry)
        item["selection_reason"] = "protected_dwell"
        # 驻留成员若本轮仍在候选里，刷新其最新指标供雷达评分
        if code in buy_map:
            item["latest_metrics"] = _candidate_metrics(buy_map[code])
        result[code] = item
    for code, row in fresh[:remaining]:
        previous_entry = previous.get(code)
        result[code] = {
            "code": code,
            "entered_at": previous_entry.get("entered_at") if previous_entry else now.isoformat(),
            "last_seen_at": now.isoformat(),
            "dwell_until": (now + timedelta(minutes=min_dwell)).isoformat(),
            "selection_reason": "rotation" if previous_entry else "fresh",
            "source_batch_id": row.get("_batch_id") or row.get("batch_id"),
            "latest_metrics": _candidate_metrics(row),
        }
    return result


# --------------------------------------------------------------------------- #
# 纯函数：哨兵与 finalizer
# --------------------------------------------------------------------------- #

def should_run_sentinel(state: dict, now: datetime, sentinel_times: list) -> Optional[str]:
    """返回本轮应当执行的最晚一个未执行的哨兵时点；无则 None。"""
    now = _ensure_aware(now)
    done = set((state or {}).get("sentinel_done") or [])
    now_hm = now.astimezone(BEIJING_TZ).strftime("%H:%M")
    due = [item for item in (sentinel_times or []) if item <= now_hm and item not in done]
    if not due:
        return None
    return max(due)


def should_finalize_session(state: dict, session: str, latest_batch: dict, now: datetime, cfg: dict) -> dict:
    """判断午间/收盘摘要是否应发送。返回 {should_send, reason, skipped_reason}。

    P0-5：除 completed/年龄外，还校验投递状态机、quality.notify_eligible、
    trade_date 与 batch_id 一致性；delivery_unknown / explicit_failed 不自动重发。
    """
    now = _ensure_aware(now)
    cfg = cfg or {}
    summary_entry = ((state or {}).get("summary_state") or {}).get(session) or {}
    status = summary_entry.get("status", "not_attempted")
    if status == "sent":
        return {"should_send": False, "reason": "already_sent", "skipped_reason": None}
    if status == "delivery_unknown":
        return {"should_send": False, "reason": "delivery_unknown", "skipped_reason": "delivery_unknown"}
    if status == "pending" and summary_entry.get("attempt_id"):
        # P0-R3：pending + attempt_id 已落盘 -> 上次投递结果不明，禁止自动重发
        return {"should_send": False, "reason": "delivery_unknown", "skipped_reason": "pending_attempt_recovered"}
    if status == "explicit_failed":
        # 明确失败允许显式手工重试；自动 finalizer 不自动重发
        return {"should_send": False, "reason": "explicit_failed", "skipped_reason": "explicit_failed"}
    if not latest_batch or latest_batch.get("status") != "completed":
        return {"should_send": False, "reason": "no_complete_batch", "skipped_reason": "no_complete_batch"}
    quality = latest_batch.get("quality") or {}
    if not quality.get("notify_eligible"):
        return {"should_send": False, "reason": "notify_not_eligible", "skipped_reason": "notify_not_eligible"}
    if latest_batch.get("trade_date") and latest_batch.get("trade_date") != now.date().isoformat():
        return {"should_send": False, "reason": "trade_date_mismatch", "skipped_reason": "trade_date_mismatch"}
    if (state or {}).get("last_batch_id") and latest_batch.get("batch_id") != (state or {}).get("last_batch_id"):
        return {"should_send": False, "reason": "batch_mismatch", "skipped_reason": "batch_mismatch"}
    ts = _parse_dt(latest_batch.get("now"))
    max_age_min = int(cfg.get("summary_max_age_min", CONFIG["summary_max_age_min"]))
    if ts is None or (now - ts).total_seconds() > max_age_min * 60:
        return {"should_send": False, "reason": "latest_batch_stale", "skipped_reason": "latest_batch_stale"}
    return {"should_send": True, "reason": "ready", "skipped_reason": None}


# --------------------------------------------------------------------------- #
# 薄 I/O 层
# --------------------------------------------------------------------------- #

def _state_path(path=None) -> Path:
    return Path(path) if path is not None else INTRADAY_STATE_FILE


def load_state(path=None, trade_date: Optional[str] = None, now: Optional[datetime] = None) -> dict:
    """读取日内状态；跨交易日自动重置；损坏时备份损坏文件并从空状态 warming。"""
    now = _ensure_aware(now) if now else datetime.now(BEIJING_TZ)
    trade_date = trade_date or now.date().isoformat()
    file_path = _state_path(path)
    state = None
    valid = False
    source_unavailable = not file_path.exists()
    if file_path.exists():
        try:
            state = json.loads(file_path.read_text(encoding="utf-8"))
            valid = isinstance(state, dict) and state.get("schema_version") == SCHEMA_VERSION
        except (json.JSONDecodeError, OSError):
            state = None
            valid = False
        if not valid:
            # P2-5：损坏状态备份后从空状态 warming，不静默丢弃
            try:
                stamp = datetime.now(BEIJING_TZ).strftime("%Y%m%dT%H%M%S")
                file_path.replace(file_path.with_suffix(file_path.suffix + f".corrupt.{stamp}"))
            except OSError:
                pass
    if not valid:
        state = empty_state(trade_date, pipeline_mode=CONFIG["pipeline_mode"])
        if source_unavailable:
            state["_source_unavailable"] = True
        else:
            state["warming_reason"] = "corrupt_state_recovered"
    return reset_state_for_trade_date(state, trade_date)


def load_state_raw(path=None) -> dict:
    """读取状态文件原样（不跨日重置、不备份），供 doctor/repair 检查历史状态。"""
    file_path = _state_path(path)
    if not file_path.exists():
        return {}
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def clean_state_for_save(state: dict) -> dict:
    """P1-R3：保存前 schema 清理，只保留正式字段，剔除 _source_unavailable / warming_reason。"""
    return {
        key: value for key, value in (state or {}).items()
        if not key.startswith("_") and key != "warming_reason"
    }


def save_state(state: dict, path=None) -> None:
    """唯一临时文件 + os.replace 原子写；自动剔除临时诊断标记。"""
    file_path = _state_path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_suffix(f".tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
    payload = _json_safe(clean_state_for_save(state))
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(tmp_path, file_path)


# --------------------------------------------------------------------------- #
# 状态存储抽象（第二轮）：把文件锁 CAS 收口到一个接口，未来可换 Redis/Postgres
# --------------------------------------------------------------------------- #

class SummaryStateStore(ABC):
    """状态存储抽象。当前只有 FileSummaryStateStore；后续可无痛换 Redis/Postgres。

    约束：所有变更都走 load → mutate → save 的原子临界区（跨进程/跨容器语义由实现保证）。
    """

    @abstractmethod
    def load(self, trade_date: Optional[str] = None, now: Optional[datetime] = None) -> dict:
        raise NotImplementedError

    @abstractmethod
    def load_raw(self) -> dict:
        raise NotImplementedError

    @abstractmethod
    def save(self, state: dict) -> None:
        raise NotImplementedError

    @abstractmethod
    def mutate(self, now: datetime, mutate_fn) -> dict:
        """在原子临界区内 load → mutate_fn(state) → save，返回新 state。"""
        raise NotImplementedError

    @abstractmethod
    def dispatch(self, session: str, snapshot_id: Optional[str], now: datetime,
                 trigger_source: str, job_id: Optional[str] = None,
                 due_at: Optional[str] = None, force: bool = False) -> Tuple[bool, dict, Optional[str]]:
        raise NotImplementedError

    @abstractmethod
    def start(self, session: str, worker_id: str, now: datetime,
              run_id: Optional[str] = None) -> Tuple[bool, dict, Optional[str]]:
        raise NotImplementedError


class FileSummaryStateStore(SummaryStateStore):
    """文件 + fcntl 锁实现（单 runner 内跨进程安全；跨 runner 依赖 git 提交 + concurrency 兜底）。"""

    def __init__(self, path=None):
        self._path = path

    def load(self, trade_date: Optional[str] = None, now: Optional[datetime] = None) -> dict:
        return load_state(path=self._path, trade_date=trade_date, now=now)

    def load_raw(self) -> dict:
        return load_state_raw(path=self._path)

    def save(self, state: dict) -> None:
        save_state(state, path=self._path)

    def mutate(self, now: datetime, mutate_fn) -> dict:
        return atomic_summary_mutate(self._path, now, mutate_fn)

    def dispatch(self, session: str, snapshot_id: Optional[str], now: datetime,
                 trigger_source: str, job_id: Optional[str] = None,
                 due_at: Optional[str] = None, force: bool = False) -> Tuple[bool, dict, Optional[str]]:
        return atomic_dispatch_summary_job(
            self._path, session, snapshot_id, now, trigger_source,
            job_id=job_id, due_at=due_at, force=force)

    def start(self, session: str, worker_id: str, now: datetime,
              run_id: Optional[str] = None) -> Tuple[bool, dict, Optional[str]]:
        return atomic_start_summary_job(self._path, session, worker_id, now, run_id=run_id)

    def recover_stale(self, now: datetime,
                      timeout_seconds: Optional[int] = None) -> Tuple[dict, List[str]]:
        return atomic_recover_stale_running(self._path, now, timeout_seconds)


# 默认存储实例（service 层统一走此实例，便于未来替换 Redis/Postgres 实现）
STATE_STORE = FileSummaryStateStore()
