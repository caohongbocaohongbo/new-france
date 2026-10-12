"""28 邮件推送适配器（Webhook 机器人 P1 可选，本轮不实现）。"""
from __future__ import annotations

from .notify import send_email_notification


def push_email(subject, text, html_content, timeout=15):
    """本轮唯一推送通道：邮件。返回 (ok, err)。"""
    return send_email_notification(subject, text, html_content, timeout=timeout)
