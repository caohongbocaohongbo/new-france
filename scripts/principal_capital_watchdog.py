"""主力资金监控看门狗：只读快照并对异常状态发送一次性告警。"""
import html
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from backend.plugins.principal_capital.config import REPORT_DIR
from backend.plugins.principal_capital.notifier import get_smtp_config, send_email
from backend.plugins.principal_capital.service import BEIJING_TZ, _fetch_snapshot_json


ALERT_NOT_STARTED = "not_started"
ALERT_SOURCE_FAILURE = "source_failure"
ALERT_OWNER_CONFLICT = "owner_conflict"
ALERT_DEGRADED = "degraded"
ALERT_FINALIZER_MISSING = "finalizer_missing"
ALERT_PENDING_STUCK = "pending_stuck"
ALERT_DELIVERY_UNKNOWN = "delivery_unknown"
ALERT_CONSISTENCY_ERROR = "consistency_error"
ALERT_M5_GAP = "m5_gap"
ALERT_M5_DAY_INVALID = "m5_day_invalid"

# 告警标题标签（owner_conflict 等不应被笼统标成「数据源失败」）
_KIND_LABELS = {
    ALERT_NOT_STARTED: "未启动",
    ALERT_SOURCE_FAILURE: "数据源失败",
    ALERT_OWNER_CONFLICT: "写者冲突",
    ALERT_DEGRADED: "质量降级",
    ALERT_FINALIZER_MISSING: "摘要未完成",
    ALERT_PENDING_STUCK: "摘要卡死",
    ALERT_DELIVERY_UNKNOWN: "投递结果不明",
    ALERT_CONSISTENCY_ERROR: "一致性错误",
    ALERT_M5_GAP: "M5 缺口",
    ALERT_M5_DAY_INVALID: "M5 当日无效",
}

STATE_FILE = REPORT_DIR / "principal_capital_watchdog_state.json"
OWNER_CONFLICT_FILE = Path(__file__).resolve().parents[1] / "data" / "principal_capital_owner_conflict.json"
INTRADAY_STATE_FILE = Path(__file__).resolve().parents[1] / "data" / "principal_capital_intraday_state.json"
M5_AUDIT_FILE = Path(__file__).resolve().parents[1] / "data" / "principal_capital_m5_audit.json"


def _as_beijing_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=BEIJING_TZ)
    return result.astimezone(BEIJING_TZ)


def _attempts_text(snapshot: Dict[str, Any]) -> str:
    attempts = ((snapshot.get("source_status") or {}).get("attempts") or [])
    if not attempts:
        return "未记录数据源尝试详情"
    lines = []
    for item in attempts:
        source = item.get("source") or "未知来源"
        status = item.get("status") or "未知状态"
        error = item.get("error") or "无错误详情"
        lines.append(f"{source}: {status}，{error}")
    return "\n".join(lines)


def _summary_alert(intraday_state: Dict[str, Any], now: datetime) -> Optional[Dict[str, str]]:
    """根据 summary_state 检查 finalizer 未完成 / pending 卡死 / delivery_unknown。"""
    summary_state = (intraday_state or {}).get("summary_state") or {}
    now_hm = now.hour * 100 + now.minute
    for session, boundary in (("am", 1130), ("pm", 1500)):
        entry = summary_state.get(session) or {}
        status = entry.get("status")
        if now_hm < boundary:
            continue
        if status == "delivery_unknown":
            return {"kind": ALERT_DELIVERY_UNKNOWN, "message": f"{session} 摘要投递结果不明（delivery_unknown），需人工确认。"}
        if status == "pending":
            updated = _as_beijing_time(entry.get("updated_at"))
            if updated is None or (now - updated).total_seconds() > 30 * 60:
                return {"kind": ALERT_PENDING_STUCK, "message": f"{session} 摘要停留在 pending 超过 30 分钟，疑似卡死。"}
        if status not in ("sent", "explicit_failed"):
            return {"kind": ALERT_FINALIZER_MISSING, "message": f"{session} 摘要 finalizer 尚未完成（状态 {status or 'not_attempted'}）。"}
    return None


def _owner_conflict_active(owner_conflict: Optional[Dict[str, Any]], now: datetime) -> bool:
    """独立 owner_conflict 诊断仅在「当日」才触发告警；跨日残留文件视为已过期。

    该诊断文件一旦写入就长期滞留在 data-snapshots 分支，若不按交易日过滤，
    watchdog 会每天都对同一份历史冲突反复误报。
    """
    if not owner_conflict or owner_conflict.get("status") != "owner_conflict":
        return False
    trade_date = owner_conflict.get("trade_date")
    if trade_date:
        return str(trade_date) == now.date().isoformat()
    oc_now = _as_beijing_time(owner_conflict.get("now"))
    return bool(oc_now and oc_now.date() == now.date())


def evaluate_snapshot(
    snapshot: Optional[Dict[str, Any]],
    now: datetime,
    owner_conflict: Optional[Dict[str, Any]] = None,
    intraday_state: Optional[Dict[str, Any]] = None,
    m5_audit: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, str]]:
    """根据快照 + 独立诊断 + 日内状态 + M5 审计判断是否需要告警。"""
    now = _as_beijing_time(now) or datetime.now(BEIJING_TZ)
    snapshot = snapshot or {}
    snapshot_time = _as_beijing_time(snapshot.get("now"))
    status = str(snapshot.get("status") or "empty")

    if snapshot_time is None or snapshot_time.date() != now.date():
        latest = snapshot_time.isoformat() if snapshot_time else "未找到有效快照时间"
        return {
            "kind": ALERT_NOT_STARTED,
            "message": f"今日主力资金监控未产生任何快照（可能未启动）。最近快照：{latest}",
        }

    if status in {"no_data", "error"}:
        return {
            "kind": ALERT_SOURCE_FAILURE,
            "message": "主力资金已运行但数据源失败/无数据。\n" + _attempts_text(snapshot),
        }

    if status == "owner_conflict" or _owner_conflict_active(owner_conflict, now):
        return {
            "kind": ALERT_OWNER_CONFLICT,
            "message": "主力资金出现唯一写者冲突（owner_conflict），正式任务未能写入。",
        }

    if status in {"partial", "degraded", "consistency_error"}:
        return {
            "kind": ALERT_DEGRADED,
            "message": f"主力资金本轮状态为 {status}（覆盖不足/质量降级/批次不一致），详见最新报告。",
        }

    # M5 当日审计检查
    records = (m5_audit or {}).get("records") or []
    today_records = [r for r in records if r.get("trade_date") == now.date().isoformat()]
    if today_records:
        if not any(r.get("valid_for_admission") for r in today_records):
            return {"kind": ALERT_M5_DAY_INVALID, "message": "今日 M5 审计轮次均无效，本日不计入连续五日。"}

    # finalizer / summary 检查
    summary_alert = _summary_alert(intraday_state, now)
    if summary_alert is not None:
        return summary_alert

    if status in {"completed", "skipped"}:
        return None

    return {
        "kind": ALERT_NOT_STARTED,
        "message": f"今日主力资金监控未产生可用快照（当前状态：{status}）。",
    }


def _load_state(state_path: Path) -> Dict[str, Any]:
    if not state_path.exists():
        return {"date": None, "sent_alerts": {}}
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"date": None, "sent_alerts": {}}
    if not isinstance(payload, dict):
        return {"date": None, "sent_alerts": {}}
    payload.setdefault("date", None)
    payload.setdefault("sent_alerts", {})
    return payload


def _save_state(state_path: Path, state: Dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _build_email(alert: Dict[str, str], snapshot: Dict[str, Any], now: datetime):
    kind_label = _KIND_LABELS.get(alert["kind"], "告警")
    snapshot_status = snapshot.get("status") or "empty"
    snapshot_time = snapshot.get("now") or "--"
    stamp = now.strftime("%Y-%m-%d %H:%M")
    subject = f"【主力资金监控告警】{kind_label} - {stamp}"
    text = (
        f"检测时间：{stamp}\n"
        f"快照状态：{snapshot_status}\n"
        f"快照时间：{snapshot_time}\n\n"
        f"{alert['message']}\n"
    )
    html_content = (
        "<html><body style='font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;padding:20px'>"
        f"<h2>主力资金监控告警：{html.escape(kind_label)}</h2>"
        f"<p>检测时间：{html.escape(stamp)}</p>"
        f"<p>快照状态：{html.escape(str(snapshot_status))}</p>"
        f"<p>快照时间：{html.escape(str(snapshot_time))}</p>"
        f"<pre style='white-space:pre-wrap'>{html.escape(alert['message'])}</pre>"
        "</body></html>"
    )
    return subject, text, html_content


def _read_local_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def run_watchdog(
    now: Optional[datetime] = None,
    snapshot_fetcher: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
    email_sender: Optional[Callable[..., tuple]] = None,
    smtp_config_loader: Optional[Callable[[], dict]] = None,
    state_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """执行一次看门狗检查，成功发送后按日期和告警类型去重。"""
    now = _as_beijing_time(now) or datetime.now(BEIJING_TZ)
    snapshot_fetcher = snapshot_fetcher or _fetch_snapshot_json
    email_sender = email_sender or send_email
    smtp_config_loader = smtp_config_loader or get_smtp_config
    state_path = Path(state_path or STATE_FILE)

    snapshot = snapshot_fetcher("principal_capital_latest.json") or {}
    owner_conflict = _read_local_json(OWNER_CONFLICT_FILE)
    intraday_state = _read_local_json(INTRADAY_STATE_FILE)
    m5_audit = _read_local_json(M5_AUDIT_FILE)
    alert = evaluate_snapshot(snapshot, now, owner_conflict, intraday_state, m5_audit)
    if alert is None:
        return {"status": "ok", "alert_type": None, "email_sent": False}

    state = _load_state(state_path)
    today = now.date().isoformat()
    sent_alerts = state.get("sent_alerts") if state.get("date") == today else {}
    if alert["kind"] in sent_alerts:
        return {
            "status": "deduplicated",
            "alert_type": alert["kind"],
            "email_sent": False,
        }

    subject, text, html_content = _build_email(alert, snapshot, now)
    email_sent, email_error = email_sender(
        subject,
        text,
        html_content,
        smtp_config_loader(),
    )
    if email_sent:
        sent_alerts[alert["kind"]] = now.isoformat()
        _save_state(state_path, {"date": today, "sent_alerts": sent_alerts})
        return {"status": "alert_sent", "alert_type": alert["kind"], "email_sent": True}
    return {
        "status": "email_error",
        "alert_type": alert["kind"],
        "email_sent": False,
        "email_error": email_error,
    }


def main() -> int:
    result = run_watchdog()
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("status") == "email_error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
