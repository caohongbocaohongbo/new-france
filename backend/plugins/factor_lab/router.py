"""06 因子实验室 API。"""
import logging

from fastapi import APIRouter, Query

from .service import read_factor_detail, read_latest

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/stats/latest")
async def factor_lab_latest():
    try:
        return read_latest()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/detail")
async def factor_lab_detail(factor: str = Query("volume_ratio"), days: int = Query(90, ge=1, le=500)):
    try:
        return {"status": "ok", "factor": factor, "records": read_factor_detail(factor, days)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
