"""28 邮件通知：复用 principal_capital.notifier.send_email。"""
from __future__ import annotations

import html as _html


def send_email_notification(subject, text, html_content, timeout=15):
    from backend.plugins.principal_capital.notifier import send_email
    return send_email(subject, text, html_content, timeout=timeout)


def _fmt(value, digits=2, default="--"):
    if value is None or value == "":
        return default
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return default


def build_observation_email(items, now, publication_kind, overall_confirmation, top_n=10):
    """构建观察期预览 / 部分确认观察清单 / 已确认观察榜邮件。"""
    ts = now.strftime("%Y-%m-%d %H:%M")
    kind_label = {
        "OBSERVATION_ONLY": "观察期预览（非推荐）",
        "CONFIRMED_LIST": "已确认观察榜单",
    }.get(publication_kind, "观察清单")
    subject = f"【低位竞价雷达】{kind_label} {ts} 共 {len(items)} 只"
    lines = [f"{kind_label} {ts}", ""]
    rows = []
    for it in items[:top_n]:
        conf = it.get("item_confirmation") or it.get("confirmation_status") or "CONFIRMED"
        missing = it.get("missing_fields") or []
        reason = it.get("confirmation_reason") or ""
        missing_txt = "；".join(str(m) for m in missing) if missing else "-"
        lines.append(
            f"#{it.get('original_rank') or it.get('rank')} {it.get('code')} {it.get('name')} "
            f"低位={_fmt(it.get('low_score'))} 竞价={_fmt(it.get('auction_score'))} "
            f"总分={_fmt(it.get('total_score'))} 确认={conf}"
            + (f" 缺失字段={missing_txt}" if conf == "UNCONFIRMED" else "")
        )
        rows.append(
            "<tr>"
            f"<td>{it.get('original_rank') or it.get('rank')}</td>"
            f"<td>{_html.escape(str(it.get('code') or ''))}</td>"
            f"<td>{_html.escape(str(it.get('name') or ''))}</td>"
            f"<td>{_fmt(it.get('low_score'))}</td>"
            f"<td>{_fmt(it.get('auction_score'))}</td>"
            f"<td>{_fmt(it.get('total_score'))}</td>"
            f"<td>{_html.escape(str(conf))}</td>"
            f"<td>{_html.escape(missing_txt)}</td>"
            f"<td>{_html.escape(str(reason))}</td>"
            "</tr>"
        )
    html_content = (
        "<html><body>"
        f"<h3>{_html.escape(subject)}</h3>"
        f"<p>整体确认状态：{_html.escape(str(overall_confirmation))}</p>"
        "<p>仅供研究与风险观察，不构成投资建议；策略验证中。</p>"
        "<table border='1' cellpadding='6' cellspacing='0'>"
        "<thead><tr><th>排名</th><th>代码</th><th>名称</th><th>低位分</th><th>竞价分</th><th>总分</th><th>确认</th><th>缺失字段</th><th>原因</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        "</body></html>"
    )
    return subject, "\n".join(lines), html_content
