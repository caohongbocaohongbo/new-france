"""龙虎榜 API。"""
import logging

from fastapi import APIRouter, Query

from .service import read_latest, read_seat_profile, read_stock_lhb

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/latest")
async def lhb_latest():
    try:
        return read_latest()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/seat/{name}")
async def lhb_seat(name: str):
    try:
        return {"status": "ok", "profile": read_seat_profile(name)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/stock/{code}")
async def lhb_stock(code: str):
    try:
        return {"status": "ok", "code": code, "records": read_stock_lhb(code)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
