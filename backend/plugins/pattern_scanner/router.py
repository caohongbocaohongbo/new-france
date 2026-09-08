"""20 形态突破选股 API。"""
import logging

from fastapi import APIRouter, Query

from backend.plugins.common import snapshot_mem_get, snapshot_mem_set

from .config import SNAPSHOT_NAME
from .service import read_code_hits, read_code_kline_with_series, read_latest

router = APIRouter()
logger = logging.getLogger(__name__)


def _latest_cached() -> dict:
    """G5：先查快照内存缓存（<1ms），无则读文件+set。"""
    cached = snapshot_mem_get(SNAPSHOT_NAME)
    if cached is not None:
        return cached
    payload = read_latest()
    snapshot_mem_set(SNAPSHOT_NAME, payload)
    return payload


@router.get("/latest")
async def pattern_scanner_latest():
    try:
        return _latest_cached()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def _filter_code_from_snapshot(code: str) -> list:
    """从快照内存 filter code（当日，不走 SQLite）。"""
    code = str(code).zfill(6)
    payload = _latest_cached()
    seen, out = set(), []
    for key in ("items", "platform_pool", "gap_pool", "dual_pool"):
        for item in payload.get(key) or []:
            c = str(item.get("code") or "").zfill(6)
            if c == code and c not in seen:
                seen.add(c)
                out.append(item)
    return out


@router.get("/{code}/kline")
async def pattern_scanner_kline(code: str, days: int = Query(80, ge=20, le=250)):
    """详情副图：日线 OHLC（平台突破/缺口标记由前端叠加）。"""
    try:
        return {"status": "ok", "code": code, **read_code_kline_with_series(code, days)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/{code}")
async def pattern_scanner_code(code: str, date: str = Query(None)):
    try:
        if date is None:
            return {"status": "ok", "code": code, "records": _filter_code_from_snapshot(code)}
        return {"status": "ok", "code": code, "records": read_code_hits(code, date)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
