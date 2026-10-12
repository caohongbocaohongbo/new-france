"""26 事件驱动雷达 API。"""
import logging

from fastapi import APIRouter, Query

from .service import read_latest, read_stock_events, run_scan_once

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/latest")
async def event_latest():
    try:
        return read_latest()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/stock/{code}")
async def event_stock(code: str):
    try:
        return {"status": "ok", "items": read_stock_events(code)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/scan")
async def event_scan():
    try:
        return run_scan_once()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
