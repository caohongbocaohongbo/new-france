"""15 四维共振 API。"""
import logging

from fastapi import APIRouter, Query

from .service import read_code_history, read_code_kline, read_latest

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/latest")
async def resonance_latest():
    try:
        return read_latest()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/{code}/kline")
async def resonance_kline(code: str, days: int = Query(60, ge=1, le=250)):
    try:
        return {"status": "ok", "code": code, "records": read_code_kline(code, days)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/{code}")
async def resonance_code(code: str, date: str = Query(None)):
    try:
        return {"status": "ok", "code": code, "records": read_code_history(code, date)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
