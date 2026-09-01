"""11 分价成交 API。"""
import logging

from fastapi import APIRouter, Query

from .service import read_code_profile, read_latest

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/latest")
async def volume_profile_latest():
    try:
        return read_latest()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/{code}")
async def volume_profile_code(code: str, date: str = Query(None)):
    try:
        return {"status": "ok", "code": code, "records": read_code_profile(code, date)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
