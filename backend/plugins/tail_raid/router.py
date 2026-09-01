"""04 尾盘抢筹 API。"""
import logging

from fastapi import APIRouter, Query

from .service import read_latest, read_stock_timeline

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/latest")
async def tail_raid_latest():
    try:
        return read_latest()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/stock/{code}")
async def tail_raid_stock(code: str, date: str = Query(None)):
    try:
        return {"status": "ok", "code": code, "records": read_stock_timeline(code, date)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
