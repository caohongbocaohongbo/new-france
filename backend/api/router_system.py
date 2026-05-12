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
        "next_execution": "15:10 (交易日)",
        "cron_expression": "10 15 * * 1-5",
        "timestamp": now.isoformat(),
    }


@router.post("/test-email")
async def test_email_endpoint():
    """发送测试邮件"""
    try:
        from ..agents.layer3_recommendation.notifier import test_email
        ok = test_email()
        if ok:
            return {"status": "ok", "message": "测试邮件已发送，请检查收件箱"}
        else:
            return {"status": "error", "message": "邮件发送失败，请检查 SMTP 配置"}
    except Exception as e:
        logger.error(f"测试邮件失败: {e}")
        return {"status": "error", "message": f"发送失败: {str(e)}"}
