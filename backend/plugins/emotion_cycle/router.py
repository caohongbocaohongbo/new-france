"""情绪周期 API。"""
import logging

from fastapi import APIRouter, Query

from .service import read_history, read_ladder, read_latest

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/latest")
async def emotion_latest():
    try:
        return read_latest()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/history")
async def emotion_history(days: int = Query(90, ge=1, le=500)):
    try:
        return {"status": "ok", "records": read_history(days)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/ladder")
async def emotion_ladder(date: str = Query(..., description="YYYY-MM-DD")):
    try:
        return {"status": "ok", "date": date, "records": read_ladder(date)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
