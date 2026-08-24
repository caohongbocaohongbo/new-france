"""smart_money_radar API。"""
import logging

from fastapi import APIRouter, Query

from .service import load_watch_pool, read_latest, run_radar_once


router = APIRouter()
logger = logging.getLogger(__name__)


def _error_response(message: str) -> dict:
    return {"status": "error", "message": str(message), "hits": []}


@router.get("/latest")
async def latest_smart_money_radar():
    try:
        return read_latest()
    except Exception as exc:
        logger.exception("读取盘中雷达 latest 失败: %s", exc)
        return _error_response(exc)


@router.get("/pool")
async def smart_money_radar_pool():
    try:
        return {"status": "completed", "items": load_watch_pool(force=True)}
    except Exception as exc:
        logger.exception("读取盘中雷达观察池失败: %s", exc)
        return _error_response(exc)


@router.post("/trigger")
async def trigger_smart_money_radar(
    dry_run: bool = Query(True),
    force: bool = Query(False),
):
    try:
        return await run_radar_once(dry_run=dry_run, force=force)
    except Exception as exc:
        logger.exception("执行盘中雷达失败: %s", exc)
        return _error_response(exc)
