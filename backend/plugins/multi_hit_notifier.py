"""18/19/20/21 多命中🔥票邮件旁路推送（复用 principal_capital.notifier.send_email + should_notify）。

设计原则（铁律「复用不重造」）：
- 邮件发送复用 principal_capital.notifier.send_email（自读 SMTP_* env）。
- 30min 冷却去重复用 principal_capital.service.should_notify。
- 去重文件按 namespace（tech/trend/pattern/chip）隔离，互不串扰。
"""
import html
import json
from datetime import date, datetime
from pathlib import Path

from backend.plugins.common import DATA_DIR
from backend.plugins.principal_capital.notifier import send_email
from backend.plugins.principal_capital.service import should_notify


def _notified_file(namespace: str, today: date) -> Path:
    return DATA_DIR / f"smart_picker_{namespace}_notified_{today.isoformat()}.json"


def load_notified_map(namespace: str, today: date) -> dict:
    path = _notified_file(namespace, today)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("notified") or {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_notified_map(namespace: str, today: date, notified_map: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _notified_file(namespace, today).write_text(
        json.dumps({"date": today.isoformat(), "notified": notified_map}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_multi_hit_email(plugin_label: str, signal_label: str, hits: list, now: datetime,
                          cols: list) -> tuple:
    """构造多命中推送邮件 subject / text / html。cols: [(key, label), ...]。"""
    count = len(hits or [])
    stamp = now.strftime("%H:%M")
    subject = "《智能选股·%s》%s🔥 %d只 - %s" % (plugin_label, signal_label, count, stamp)

    lines = ["智能选股·%s %s 新增 %d 只 %s" % (plugin_label, signal_label, count, stamp), ""]
    rows = []
    for it in hits or []:
        cells = "  ".join("%s=%s" % (label, it.get(key)) for key, label in cols)
        lines.append("  %s %s" % (it.get("code"), cells))
        tds = ["<td>%s</td>" % html.escape(str(it.get("code") or ""))]
        for key, _ in cols:
            val = it.get(key)
            tds.append("<td>%s</td>" % html.escape("--" if val is None else str(val)))
        rows.append("<tr>%s</tr>" % "".join(tds))
    lines.append("")
    lines.append("提示：信号仅为辅助参考，不构成投资建议。")

    head = "<th>代码</th>" + "".join("<th>%s</th>" % html.escape(label) for _, label in cols)
    html_content = ("<html><body>"
        "<h3>%s</h3>"
        "<table border='1' cellpadding='6' cellspacing='0'>"
        "<thead><tr>%s</tr></thead><tbody>%s</tbody></table>"
        "<p>提示：信号仅为辅助参考，不构成投资建议。</p>"
        "</body></html>") % (html.escape(subject), head, "".join(rows))
    return subject, "\n".join(lines), html_content


def notify_multi_hit(plugin_label: str, signal_label: str, hits: list, now: datetime,
                     cols: list, smtp_config: dict = None, timeout: int = 15) -> tuple:
    """推送多命中信号。返回 (sent: bool, error)。"""
    if not hits:
        return False, None
    subject, text, html_content = build_multi_hit_email(plugin_label, signal_label, hits, now, cols)
    return send_email(subject, text, html_content, smtp_config, timeout)


def push_multi_hit(namespace: str, plugin_label: str, signal_label: str, new_hits: list,
                   now: datetime, cooldown_minutes: int, cols: list, notifier=None) -> tuple:
    """新增多命中票的冷却去重 + 邮件推送。返回 (email_sent, notified_codes, email_error)。

    - new_hits：已按「当日快照对比」筛出的新增信号票（调用方负责计算）。
    - 冷却去重：should_notify 以 30min 为窗口，防止盘中重跑重复刷屏。
    - notifier：可注入 mock（测试用），默认 notify_multi_hit。
    """
    if not new_hits:
        return False, set(), None
    notified_map = load_notified_map(namespace, now.date())
    pending = [h for h in new_hits
               if should_notify(h["code"], namespace, now, notified_map, int(cooldown_minutes))]
    if not pending:
        return False, set(), None
    sender = notifier or notify_multi_hit
    sent, err = sender(plugin_label, signal_label, pending, now, cols)
    if not sent:
        return False, set(), err
    notified_codes = set()
    for h in pending:
        code = str(h.get("code") or "").zfill(6)
        notified_map.setdefault(code, []).append(now.isoformat())
        notified_codes.add(code)
    save_notified_map(namespace, now.date(), notified_map)
    return True, notified_codes, None
