"""smart_money_radar API。"""
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Query

from .config import REPORT_DIR
from .service import _STORE, load_watch_pool, read_latest, run_radar_once


router = APIRouter()
logger = logging.getLogger(__name__)


def _error_response(message: str) -> dict:
    return {"status": "error", "message": str(message), "hits": []}


def _read_report_file(name: str) -> dict:
    path = REPORT_DIR / f"{name}_latest.json"
    if not path.exists():
        return {"status": "not_run", "items": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "error", "items": []}


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


# ---- 07 盘口委托失衡 ----
@router.get("/orderbook/latest")
async def orderbook_latest():
    return _read_report_file("orderbook")


# ---- 08 逐笔成交行为 ----
@router.get("/orderflow/latest")
async def orderflow_latest():
    return _read_report_file("orderflow")


# ---- 13 集合竞价异动 ----
@router.get("/auction/latest")
async def auction_latest():
    return _read_report_file("auction")


@router.get("/auction/{code}")
async def auction_code(code: str):
    from .auction import evaluate_auction

    frames = list((_STORE.auction_frames or {}).get(code.zfill(6), []))
    if not frames:
        return {"status": "no_data", "code": code, "frames": []}
    name = (frames[-1].get("quote") or {}).get("name") or ""
    return {"status": "ok", "code": code, "summary": evaluate_auction(code, name, frames), "frames": frames}
