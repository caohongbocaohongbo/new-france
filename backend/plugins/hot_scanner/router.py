"""26 舆情热度聚合 API。"""
import logging

from fastapi import APIRouter, Query

from .service import read_history, read_latest, run_scan_once

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/latest")
async def hot_latest():
    try:
        return read_latest()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/history")
async def hot_history(days: int = Query(30, ge=1, le=365)):
    try:
        return {"status": "ok", "records": read_history(days)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/scan")
async def hot_scan():
    try:
        return run_scan_once()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
