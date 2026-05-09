"""事件 API"""
from fastapi import APIRouter, Query
from typing import Optional

router = APIRouter()


@router.get("/today")
async def get_today_events():
    """今日事件"""
    return {"date": "", "events": [], "total": 0}


@router.get("/upcoming")
async def get_upcoming_events(days: int = Query(7, ge=1, le=30)):
    """未来N天事件"""
    return {"days": days, "events": []}


@router.get("/calendar")
async def get_calendar(month: Optional[str] = Query(None)):
    """事件日历视图"""
    return {"month": month, "days": {}}


@router.get("/stock/{code}")
async def get_stock_events(code: str):
    """某只股票的相关事件"""
    return {"code": code, "events": []}
