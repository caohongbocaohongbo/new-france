"""26 绩效模拟盘 API。"""
import logging

from fastapi import APIRouter, Query

from .service import open_positions_from_recommendations, read_summary, settle_positions

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/summary")
async def paper_summary():
    try:
        return read_summary()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/positions")
async def paper_positions():
    try:
        s = read_summary()
        return {"status": "ok", "open": s.get("open_positions", []), "closed": s.get("closed_positions", [])}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/nav")
async def paper_nav(days: int = Query(90, ge=1, le=500)):
    try:
        s = read_summary()
        navs = s.get("nav", [])
        return {"status": "ok", "records": navs[-days:] if days else navs}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/run-open")
async def paper_run_open(date: str = Query(None, description="YYYY-MM-DD，缺省今日")):
    try:
        return open_positions_from_recommendations(date)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/run-settle")
async def paper_run_settle(date: str = Query(None, description="YYYY-MM-DD，缺省今日")):
    try:
        return settle_positions(date)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
