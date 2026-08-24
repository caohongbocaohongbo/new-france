"""盘中雷达邮件封装。"""
import html
from datetime import datetime

from backend.plugins.principal_capital.notifier import send_email

from .config import CONFIG


def _tag() -> str:
    return "《本地雷达》" if CONFIG.get("radar_source") == "local" else "《云端雷达》"


def build_payload(hits: list, source: str, now: datetime) -> tuple:
    tag = _tag()
    subject = f"{tag} 盘中信号 {len(hits)}只 {now.strftime('%H:%M')}"
    priority = {"启动前夕": 0, "启动": 0, "吸筹确认": 1, "潜伏": 2, "启动失败": 3, "观察池": 4}
    hits = sorted(hits, key=lambda item: (priority.get(item.get("stage"), 9), -(float(item.get("smart_money_score") or 0))))
    lines = [f"{tag} {source} 盘中雷达命中 {len(hits)} 只", ""]
    rows = []
    for i, item in enumerate(hits):
        rank = i + 1
        if i == 0 or item.get("stage") != hits[i - 1].get("stage"):
            lines.append(f"【{item.get('stage') or '未分阶段'}】")
            color = "#b91c1c" if item.get("stage") in {"启动前夕", "启动"} else "#374151"
            rows.append(
                f"<tr><th colspan='10' style='text-align:left;color:{color}'>"
                f"{html.escape(str(item.get('stage') or '未分阶段'))}</th></tr>"
            )
        lines.append(
            f"{rank}. {item.get('code')} {item.get('name')} "
            f"阶段={item.get('stage')} SmartMoney={item.get('smart_money_score') or '--'} "
            f"Launch={item.get('launch_score') or '--'} 背离度={item.get('price_impact') or '--'}"
        )
        rows.append(
            "<tr>"
            f"<td>{rank}</td>"
            f"<td>{html.escape(str(item.get('code') or ''))}</td>"
            f"<td>{html.escape(str(item.get('name') or ''))}</td>"
            f"<td>{html.escape(str(item.get('stage') or ''))}</td>"
            f"<td>{item.get('smart_money_score') or '--'}</td>"
            f"<td>{item.get('launch_score') or '--'}</td>"
            f"<td>{item.get('price_impact') or '--'}</td>"
            f"<td>{item.get('strength_amt') or '--'}</td>"
            f"<td>{item.get('active_buy_ratio') or '--'}</td>"
            f"<td>{item.get('main_inflow_ratio') or '--'}</td>"
            "</tr>"
        )
    html_content = (
        "<html><body>"
        f"<h3>{html.escape(subject)}</h3>"
        "<p>提示：启动条件正在形成，请结合盘面自行判断。</p>"
        "<table border='1' cellpadding='6' cellspacing='0'>"
        "<thead><tr><th>排名</th><th>代码</th><th>名称</th><th>阶段</th><th>SmartMoney</th><th>Launch</th><th>背离度</th><th>盘口强度</th><th>主动买占比</th><th>大单买入占比</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        "</body></html>"
    )
    return subject, "\n".join(lines), html_content


def build_and_send(hits: list, source: str, now: datetime, timeout: int = 15):
    if not hits:
        return False, None
    subject, text, html_content = build_payload(hits, source, now)
    return send_email(subject, text, html_content, timeout=timeout)
