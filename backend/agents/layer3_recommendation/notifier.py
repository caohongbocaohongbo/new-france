"""
通知模块 — 邮件通知（Brevo HTTP API 优先，SMTP 备选）
"""
import os
import json
import smtplib
import logging
from email.mime.text import MIMEText
from datetime import date
from typing import List, Optional

logger = logging.getLogger(__name__)

NOTIFY_CONFIG = {
    "email_enabled": True,
    "email_user": os.environ.get("SMTP_USER", ""),
    "email_to": os.environ.get("SMTP_TO", os.environ.get("SMTP_USER", "")),
}
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")

WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def send_notification(scored_stocks, target_date: date,
                      index_gain: float = 0.0,
                      report_path: str = "",
                      zt_list: list = None) -> bool:
    """发送邮件通知"""
    if not NOTIFY_CONFIG["email_enabled"]:
        logger.info("邮件通知未启用")
        return False

    if zt_list is None:
        zt_list = []
    content = _build_email_content(scored_stocks, target_date, index_gain, zt_list)
    ok, _ = _send_email(
        subject=f"New France 涨停回撤推荐 - {target_date.strftime('%Y-%m-%d')}",
        content=content,
    )
    return ok


def _build_email_content(stocks, target_date, index_gain, zt_list: list) -> str:
    date_str = target_date.strftime("%Y-%m-%d")
    weekday = WEEKDAY_CN[target_date.weekday()]

    # 读取监控列表概况
    try:
        from pathlib import Path
        france_file = Path(__file__).resolve().parent.parent.parent.parent / "data" / "france.md"
        if france_file.exists():
            import re
            wl_lines = [l for l in france_file.read_text(encoding="utf-8").split("\n")
                       if re.match(r"\|\s*\d{6}\s*\|", l)]
            wl_total = len(wl_lines)
        else:
            wl_total = 0
    except Exception:
        wl_total = 0

    lines = [
        f"New France 涨停回撤战法 - {date_str} {weekday}",
        "=" * 40,
        f"上证指数涨幅: {index_gain:+.2f}%",
        f"监控股票总数: {wl_total} 只",
        f"今日新增涨停: {len(zt_list)} 只",
        "",
    ]

    # ---- 今日涨停股列表 ----
    if zt_list:
        lines.append("【今日涨停股列表】")
        lines.append("")
        for zt in zt_list:
            mcap = zt.get('mcap', 0)
            mcap_str = f"{mcap/1e8:.1f}亿" if mcap and mcap > 0 else "--"
            fbt = zt.get('seal_time', 0)
            fbt_str = f"{fbt//1000000:02d}:{(fbt%1000000)//10000:02d}" if fbt and fbt > 0 else "--"
            lines.append(
                f"  {zt['code']} {zt['name']}  "
                f"现价:{zt.get('price',0):.2f}  "
                f"涨幅:{zt.get('change_pct',0):+.2f}%  "
                f"换手:{zt.get('turnover',0):.1f}%  "
                f"量比:{zt.get('vol_ratio') or '--'}  "
                f"PE:{zt.get('pe') or '--'}  "
                f"市值:{mcap_str}  "
                f"封板:{fbt_str}  "
                f"炸板:{int(zt.get('break_count',0))}次  "
                f"连板:{int(zt.get('consecutive',0))}天"
            )
        lines.append("")

    strong = [s for s in stocks if s.recommendation == "STRONG_BUY"]
    buy = [s for s in stocks if s.recommendation == "BUY"]
    watch = [s for s in stocks if s.recommendation == "WATCH"]

    lines.append(f"【今日筛选结果】STRONG_BUY {len(strong)} | BUY {len(buy)} | WATCH {len(watch)}")
    lines.append("")

    if not stocks:
        lines.append("今日无符合条件的回撤买入信号。")
        lines.append("涨停股已加入监控列表，待回撤 3-10% 后进入筛选范围。")
        lines.append("")
    else:
        if strong:
            lines.append(f"--- STRONG_BUY 强烈买入 ({len(strong)}只) ---")
            lines.append("")
            for s in strong:
                _append_stock_detail(lines, s)
            lines.append("")

        if buy:
            lines.append(f"--- BUY 建议买入 ({len(buy)}只) ---")
            lines.append("")
            for s in buy:
                _append_stock_detail(lines, s)
            lines.append("")

        if watch:
            lines.append(f"--- WATCH 观察 ({len(watch)}只) ---")
            lines.append("")
            for s in watch:
                _append_stock_detail(lines, s)
            lines.append("")

    lines.append("-" * 40)
    lines.append("")
    lines.append("【查看完整数据】")
    lines.append("前端页面: https://new-france.onrender.com")
    lines.append("")
    lines.append("【策略说明】")
    lines.append("1. 每日15:10抓取涨停股池 → 加入监控列表")
    lines.append("2. 次日开始追踪回撤，3-10%回撤区间触发买入信号")
    lines.append("3. 理财有风险，投资需谨慎，本结果仅供参考")

    return "\n".join(lines)


def _append_stock_detail(lines, s):
    """格式化单只推荐股票的详细信息"""
    stars = "★" * min(4, max(1, int(s.adjusted_score / 25)))
    level_cn = {"STRONG_BUY": "强烈买入", "BUY": "建议买入", "WATCH": "观察"}
    lines.append(f"[{level_cn.get(s.recommendation, s.recommendation)}] "
                 f"{s.name}({s.code}) 得分:{s.adjusted_score:.0f} {stars}")
    lines.append(f"  回撤:{s.drop_pct:+.2f}% | 排名:#{s.rank}")
    lines.append("  评分详情:")
    for key, r in s.factor_scores.items():
        if key == "event_bonus":
            continue
        mark = "✓" if r.passed else "✗"
        lines.append(f"    {mark} {r.name}({r.weight*100:.0f}%): {r.detail}")
    lines.append("")


def _send_via_brevo(subject: str, content: str) -> tuple[bool, str]:
    """通过 Brevo HTTP API 发送邮件（Render 上 SMTP 端口被封，走 HTTP）"""
    try:
        import requests
        resp = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "api-key": BREVO_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "sender": {
                    "email": NOTIFY_CONFIG["email_user"] or "noreply@new-france.onrender.com",
                    "name": "New France 选股系统",
                },
                "to": [{"email": NOTIFY_CONFIG["email_to"]}],
                "subject": subject,
                "textContent": content,
            },
            timeout=15,
        )
        if resp.status_code in (200, 201):
            logger.info(f"[Brevo] 邮件已发送至 {NOTIFY_CONFIG['email_to']}")
            return True, "OK"
        err = f"Brevo API 返回 {resp.status_code}: {resp.text[:200]}"
        logger.error(err)
        return False, err
    except Exception as e:
        err = f"Brevo 发送异常: {type(e).__name__}: {e}"
        logger.error(err)
        return False, err


def _send_via_smtp(subject: str, content: str) -> tuple[bool, str]:
    """通过 SMTP 发送邮件（本地 / GitHub Actions 使用）"""
    host = os.environ.get("SMTP_HOST", "smtp.qq.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = NOTIFY_CONFIG["email_user"]
    password = SMTP_PASSWORD

    if not password:
        return False, "SMTP密码未配置"
    if not user:
        return False, "SMTP_USER 未配置"

    try:
        msg = MIMEText(content, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = NOTIFY_CONFIG["email_to"]

        if port in (587, 25):
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.starttls()
                server.login(user, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP_SSL(host, port, timeout=15) as server:
                server.login(user, password)
                server.send_message(msg)

        logger.info(f"[SMTP] 邮件已发送至 {NOTIFY_CONFIG['email_to']}")
        return True, "OK"
    except smtplib.SMTPAuthenticationError as e:
        err = f"SMTP 登录失败: {user}, 错误={e}"
        logger.error(err)
        return False, err
    except smtplib.SMTPConnectError as e:
        err = f"SMTP 连接失败: {host}:{port}, 错误={e}"
        logger.error(err)
        return False, err
    except OSError as e:
        err = f"SMTP 网络不通: {e}"
        logger.error(err)
        return False, err
    except Exception as e:
        err = f"SMTP 异常: {type(e).__name__}: {e}"
        logger.error(err)
        return False, err


def _send_email(subject: str, content: str) -> tuple[bool, str]:
    """发送邮件：Brevo HTTP API 优先 → SMTP 备选"""
    if BREVO_API_KEY:
        return _send_via_brevo(subject, content)
    return _send_via_smtp(subject, content)


def test_email() -> tuple[bool, str]:
    """发送测试邮件"""
    return _send_email(
        subject="[测试] New France 涨停回撤战法",
        content="这是一封测试邮件。\n如果收到说明SMTP配置正确！\n\nNew France v1.0",
    )
