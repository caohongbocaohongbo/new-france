"""主力资金插件独立邮件发送（不依赖 backend/agents/layer3_recommendation/notifier）。

设计原则：插件自带 SMTP 实现，仅读取 SMTP_* 环境变量。这样插件迁移到新项目时
无需关心原项目的邮件模块。
"""
import html
import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional, Tuple

from .config import CONFIG

logger = logging.getLogger(__name__)


def get_smtp_config() -> dict:
    """读取 SMTP 配置（优先环境变量，回退到 CONFIG）。"""
    return {
        "host": os.environ.get("SMTP_HOST", CONFIG["smtp_host"]),
        "port": int(os.environ.get("SMTP_PORT", CONFIG["smtp_port"])),
        "user": os.environ.get("SMTP_USER", CONFIG["smtp_user"]),
        "password": os.environ.get("SMTP_PASSWORD", CONFIG["smtp_password"]),
        "to": os.environ.get("SMTP_TO", CONFIG["smtp_to"]),
    }


def send_email(subject: str, text_content: str, html_content: str,
               smtp_config: Optional[dict] = None,
               timeout: int = 15) -> Tuple[bool, Optional[str]]:
    """发送邮件。返回 (success, error_message)。

    支持 SSL(465) 和 STARTTLS(587/25) 两种连接方式。
    """
    cfg = smtp_config or get_smtp_config()
    host, port = cfg["host"], int(cfg["port"])
    user, password = cfg["user"], cfg["password"]
    to_addr = cfg["to"]

    if not user:
        return False, "SMTP_USER 未配置"
    if not password:
        return False, "SMTP_PASSWORD 未配置"
    if not to_addr:
        return False, "SMTP_TO 未配置"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr
    msg.attach(MIMEText(text_content, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        if port == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=timeout, context=context) as server:
                server.login(user, password)
                server.sendmail(user, [to_addr], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(user, password)
                server.sendmail(user, [to_addr], msg.as_string())
        return True, None
    except smtplib.SMTPException as exc:
        logger.warning("SMTP 发送失败: %s", exc)
        return False, f"SMTPException: {exc}"
    except (OSError, ssl.SSLError) as exc:
        logger.warning("SMTP 网络/SSL 错误: %s", exc)
        return False, f"NetworkError: {exc}"

def build_summary_payload(buy_today: list, session: str, now) -> tuple:
    """构造午间/收盘「日内汇总」邮件（标题固定带“日内汇总”，不复用实时卖出参考模板）。"""
    label = "上午" if session == "am" else "全天"
    stamp = now.strftime("%Y-%m-%d %H:%M")
    rows = [row for row in (buy_today or []) if isinstance(row, dict)]
    count = len(rows)
    subject = f"[日内汇总] 主力吸筹候选 {count} 只（{label}） - {stamp}"

    def _line(row: dict) -> str:
        code = str(row.get("code") or "").zfill(6)
        name = str(row.get("name") or "")
        ratio = row.get("main_inflow_ratio")
        net = row.get("main_net_inflow")
        ratio_text = "--" if ratio is None else f"{float(ratio):+.2f}%"
        net_text = "--" if net is None else f"{float(net) / 1e8:.2f}亿"
        return f"{code} {name} 占比{ratio_text} 主力{net_text}"

    if rows:
        text = f"主力资金日内汇总（{label}，共 {count} 只吸筹候选）\n\n" + "\n".join(_line(row) for row in rows) + "\n"
    else:
        text = f"主力资金日内汇总（{label}）\n\n暂无吸筹候选\n"

    def _html_row(row: dict) -> str:
        code = html.escape(str(row.get("code") or "").zfill(6))
        name = html.escape(str(row.get("name") or ""))
        ratio = row.get("main_inflow_ratio")
        net = row.get("main_net_inflow")
        ratio_html = html.escape("--" if ratio is None else "{:+.2f}%".format(float(ratio)))
        net_html = html.escape("--" if net is None else "{:.2f}亿".format(float(net) / 1e8))
        return (
            "<tr><td>" + code + "</td><td>" + name + "</td><td>" + ratio_html +
            "</td><td>" + net_html + "</td></tr>"
        )

    html_rows = "".join(_html_row(row) for row in rows) or '<tr><td colspan="4">暂无吸筹候选</td></tr>'
    html_content = (
        "<!DOCTYPE html><html><body style='font-family:-apple-system,BlinkMacSystemFont,sans-serif;padding:20px;color:#111827'>"
        f"<h2>主力资金日内汇总（{label}）</h2>"
        "<table style='width:100%;border-collapse:collapse' border='1' cellspacing='0' cellpadding='8'>"
        "<tr style='background:#fef2f2'><th>代码</th><th>名称</th><th>占比</th><th>主力净流入</th></tr>"
        f"{html_rows}</table></body></html>"
    )
    return subject, text, html_content
