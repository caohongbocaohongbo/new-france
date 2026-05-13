"""系统 API — 健康检查、状态、配置"""
from fastapi import APIRouter
from datetime import datetime, timezone, timedelta
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

BEIJING_TZ = timezone(timedelta(hours=8))


@router.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(BEIJING_TZ).isoformat()}


@router.get("/status")
async def system_status():
    """系统运行状态（北京时间）"""
    now = datetime.now(BEIJING_TZ)
    is_trading = now.weekday() < 5 and 9 <= now.hour <= 15
    return {
        "is_trading_day": now.weekday() < 5,
        "is_trading_hours": is_trading,
        "next_execution": "15:10 (北京时间, 交易日)",
        "cron_expression": "每个交易日 15:10 (北京时间)",
        "timestamp": now.isoformat(),
    }


@router.post("/test-email")
async def test_email_endpoint():
    """发送测试邮件"""
    try:
        from ..agents.layer3_recommendation.notifier import test_email, BREVO_API_KEY, NOTIFY_CONFIG
        import os
        ok, msg = test_email()
        return {
            "status": "ok" if ok else "error",
            "message": msg,
            "debug": {
                "brevo_key_set": bool(BREVO_API_KEY),
                "brevo_key_len": len(BREVO_API_KEY),
                "email_user": NOTIFY_CONFIG["email_user"],
                "email_to": NOTIFY_CONFIG["email_to"],
            }
        }
    except Exception as e:
        logger.error(f"测试邮件失败: {e}")
        return {"status": "error", "message": f"发送失败: {str(e)}"}
