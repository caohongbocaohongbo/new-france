"""05 板块轮动 API。"""
import logging

from fastapi import APIRouter, Query

from .service import read_latest

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/latest")
async def board_latest():
    try:
        return read_latest()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
