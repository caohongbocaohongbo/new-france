"""尾盘隔夜套利插件独立邮件发送（不依赖 backend/agents/layer3_recommendation/notifier）。

设计原则：插件自带 SMTP 实现，仅读取 SMTP_* 环境变量。这样插件迁移到新项目时
无需关心原项目的邮件模块。
"""
import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional, Tuple

import requests

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
        "brevo_api_key": os.environ.get("BREVO_API_KEY", ""),
    }


def _send_via_brevo(subject: str, text_content: str, html_content: str,
                     config: dict, timeout: int) -> Tuple[bool, Optional[str]]:
    """通过 HTTPS 调用 Brevo，适配 Render 免费 Web Service 的网络限制。"""
    api_key = str(config.get("brevo_api_key") or "").strip()
    if not api_key:
        return False, "BREVO_API_KEY 未配置"
    sender = str(config.get("user") or "").strip()
    recipients = [
        email.strip()
        for email in str(config.get("to") or "").split(",")
        if email.strip()
    ]
    if not sender:
        return False, "SMTP_USER/Brevo发件人未配置"
    if not recipients:
        return False, "SMTP_TO/Brevo收件人未配置"

    try:
        payload = {
            "sender": {"email": sender, "name": "New France 尾盘隔夜套利"},
            "to": [{"email": email} for email in recipients],
            "subject": subject,
            "textContent": text_content,
            "htmlContent": html_content,
        }
        idempotency_key = str(config.get("idempotency_key") or "").strip()
        if idempotency_key:
            payload["headers"] = {"Idempotency-Key": idempotency_key}

        response = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        logger.warning("Brevo HTTPS 请求失败: %s", exc)
        return False, f"Brevo请求失败: {exc}"

    if response.status_code in (200, 201, 202):
        return True, None
    error = f"Brevo API 返回 {response.status_code}: {response.text[:200]}"
    logger.warning(error)
    return False, error


def send_email(subject: str, text_content: str, html_content: str,
               smtp_config: Optional[dict] = None,
               timeout: int = 15) -> Tuple[bool, Optional[str]]:
    """发送邮件。返回 (success, error_message)。

    支持 SSL(465) 和 STARTTLS(587/25) 两种连接方式。
    """
    cfg = smtp_config or get_smtp_config()
    if str(cfg.get("brevo_api_key") or "").strip():
        return _send_via_brevo(subject, text_content, html_content, cfg, timeout)

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
