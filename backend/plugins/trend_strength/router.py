"""19 趋势强度选股 API。"""
import logging

from fastapi import APIRouter, Query

from .service import read_code_hits, read_latest

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/latest")
async def trend_strength_latest():
    try:
        return read_latest()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/{code}")
async def trend_strength_code(code: str, date: str = Query(None)):
    try:
        return {"status": "ok", "code": code, "records": read_code_hits(code, date)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
