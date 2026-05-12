"""
通知模块 — 邮件通知
"""
import os
import smtplib
import logging
from email.mime.text import MIMEText
from datetime import date
from typing import List, Optional

logger = logging.getLogger(__name__)

NOTIFY_CONFIG = {
    "email_enabled": True,
    "email_host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
    "email_port": int(os.environ.get("SMTP_PORT", "465")),
    "email_user": os.environ.get("SMTP_USER", ""),
    "email_to": os.environ.get("EMAIL_TO", os.environ.get("SMTP_USER", "")),
}
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def send_notification(scored_stocks, target_date: date,
                      index_gain: float = 0.0,
                      report_path: str = "") -> bool:
    """发送邮件通知"""
    if not NOTIFY_CONFIG["email_enabled"]:
        logger.info("邮件通知未启用")
        return False

    content = _build_email_content(scored_stocks, target_date, index_gain)
    return _send_email(
        subject=f"New France 涨停回撤推荐 - {target_date.strftime('%Y-%m-%d')}",
        content=content,
    )


def _build_email_content(stocks, target_date, index_gain) -> str:
    date_str = target_date.strftime("%Y-%m-%d")
    weekday = WEEKDAY_CN[target_date.weekday()]

    lines = [
        f"New France 涨停回撤战法 - {date_str} {weekday}",
        "=" * 40,
        f"上证指数涨幅: {index_gain:+.2f}%",
        "",
    ]

    strong = [s for s in stocks if s.recommendation == "STRONG_BUY"]
    buy = [s for s in stocks if s.recommendation == "BUY"]

    lines.append(f"【推荐汇总】STRONG_BUY {len(strong)} 只 | BUY {len(buy)} 只")
    lines.append("")

    for s in strong + buy:
        stars = "\u2605" * min(4, max(1, int(s.adjusted_score / 25)))
        level_cn = {"STRONG_BUY": "强烈买入", "BUY": "建议买入"}
        pe = s.factor_scores.get("pe")
        pe_str = ""
        if pe:
            pe_str = f" PE详情: {pe.detail}"
        lines.append(f"[{level_cn.get(s.recommendation, s.recommendation)}] "
                     f"{s.name}({s.code}) 得分:{s.adjusted_score:.0f} {stars}")
        lines.append(f"  回撤:{s.drop_pct:+.2f}% | 排名:#{s.rank}")
        lines.append(f"  评分详情:")
        for key, r in s.factor_scores.items():
            if key == "event_bonus":
                continue
            mark = "\u2713" if r.passed else "\u2717"
            lines.append(f"    {mark} {r.name}({r.weight*100:.0f}%): {r.detail}")
        lines.append("")

    lines.append("-" * 40)
    lines.append("")
    lines.append("【特别提醒】")
    lines.append("1. 本策略追踪曾涨停股票，在回撤5%-10%时寻找买点")
    lines.append("2. 理财有风险，投资需谨慎，本结果仅供参考")
    lines.append("3. 所有推荐基于量化因子评分，需结合盘感综合判断")

    return "\n".join(lines)


def _send_email(subject: str, content: str) -> bool:
    password = SMTP_PASSWORD
    if not password:
        logger.warning("SMTP密码未配置")
        return False

    try:
        msg = MIMEText(content, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = NOTIFY_CONFIG["email_user"]
        msg["To"] = NOTIFY_CONFIG["email_to"]

        with smtplib.SMTP_SSL(NOTIFY_CONFIG["email_host"],
                              NOTIFY_CONFIG["email_port"],
                              timeout=15) as server:
            server.login(NOTIFY_CONFIG["email_user"], password)
            server.send_message(msg)

        logger.info(f"邮件已发送至 {NOTIFY_CONFIG['email_to']}")
        return True
    except Exception as e:
        logger.error(f"邮件发送失败: {e}")
        return False


def test_email() -> bool:
    """发送测试邮件"""
    return _send_email(
        subject="[测试] New France 涨停回撤战法",
        content="这是一封测试邮件。\n如果收到说明SMTP配置正确！\n\nNew France v1.0",
    )
