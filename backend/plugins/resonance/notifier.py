"""17 四维共振盘中实时提醒（复用 principal_capital.notifier.send_email）。"""
import html
from datetime import datetime

from backend.plugins.principal_capital.notifier import send_email


def build_email_payload(new_red, new_green, now):
    """构造盘中推送邮件 subject / text / html。"""
    red_count = len(new_red or [])
    green_count = len(new_green or [])
    stamp = now.strftime("%H:%M")
    tags = []
    if red_count:
        tags.append("红灯%d" % red_count)
    if green_count:
        tags.append("绿灯%d" % green_count)
    subject = "《四维共振·盘中》 %s - %s" % (" ".join(tags), stamp)

    lines = ["四维共振盘中新增信号 %s" % stamp, ""]
    rows = []
    for title, items in (("红灯（主力进场）", new_red or []), ("绿灯（主力出场）", new_green or [])):
        if not items:
            continue
        lines.append("【%s】" % title)
        for it in items:
            lines.append("  %s %s 共振分=%s D1=%s D2=%s D3=%s D4=%s" % (
                it.get("code"), it.get("name"), it.get("resonance_score"),
                it.get("d1_state"), it.get("d2_score"), it.get("d3_score"), it.get("d4_score"),
            ))
            rows.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                html.escape(str(it.get("code") or "")), html.escape(str(it.get("name") or "")),
                it.get("resonance_score"), html.escape(str(it.get("d1_state") or "")),
                it.get("d2_score"), it.get("d3_score"), it.get("d4_score"),
            ))
    lines.append("")
    lines.append("提示：信号仅为辅助参考，不构成投资建议。")

    html_content = ("<html><body>"
        "<h3>%s</h3>"
        "<table border='1' cellpadding='6' cellspacing='0'>"
        "<thead><tr><th>代码</th><th>名称</th><th>共振分</th><th>D1状态</th><th>D2</th><th>D3</th><th>D4</th></tr></thead>"
        "<tbody>%s</tbody></table>"
        "<p>提示：信号仅为辅助参考，不构成投资建议。</p>"
        "</body></html>") % (html.escape(subject), "".join(rows))
    return subject, "\n".join(lines), html_content


def notify_new_signals(new_red, new_green, now, smtp_config=None, timeout=15):
    """推送新增 RED/GREEN 信号。返回 (sent: bool, error)。"""
    if not new_red and not new_green:
        return False, None
    subject, text, html_content = build_email_payload(new_red, new_green, now)
    return send_email(subject, text, html_content, smtp_config, timeout)
