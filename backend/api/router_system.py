"""系统 API — 健康检查、状态、配置"""
from fastapi import APIRouter
from datetime import datetime

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@router.get("/status")
async def system_status():
    """系统运行状态"""
    now = datetime.now()
    is_trading = now.weekday() < 5 and 9 <= now.hour <= 15
    return {
        "is_trading_day": now.weekday() < 5,
        "is_trading_hours": is_trading,
        "next_execution": "15:10 (交易日)",
        "cron_expression": "10 15 * * 1-5",
        "timestamp": now.isoformat(),
    }
