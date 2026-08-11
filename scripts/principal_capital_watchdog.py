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
STATE_FILE = REPORT_DIR / "principal_capital_watchdog_state.json"


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


def evaluate_snapshot(snapshot: Optional[Dict[str, Any]], now: datetime) -> Optional[Dict[str, str]]:
    """根据当天快照判断是否需要发送未启动或数据源失败告警。"""
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
    kind_label = "未启动" if alert["kind"] == ALERT_NOT_STARTED else "数据源失败"
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
    alert = evaluate_snapshot(snapshot, now)
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
