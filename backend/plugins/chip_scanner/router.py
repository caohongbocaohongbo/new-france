"""21 筹码集中度与获利盘选股 API。"""
import logging

from fastapi import APIRouter, Query

from backend.plugins.common import read_snapshot_resilient, snapshot_mem_get, snapshot_mem_set

from .config import SNAPSHOT_NAME
from .service import (
    read_code_distribution, read_code_hits, read_code_kline_with_series,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _latest_cached() -> dict:
    """G3：先查快照内存缓存（<1ms），无则本地文件优先、data-snapshots raw 兜底（22 方案读入口）。"""
    cached = snapshot_mem_get(SNAPSHOT_NAME)
    if cached is not None:
        return cached
    payload = read_snapshot_resilient(SNAPSHOT_NAME)
    snapshot_mem_set(SNAPSHOT_NAME, payload)
    return payload


@router.get("/latest")
async def chip_scanner_latest():
    try:
        return _latest_cached()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def _filter_code_from_snapshot(code: str) -> list:
    """G3：从快照内存 filter code（当日，不走 SQLite）。"""
    code = str(code).zfill(6)
    payload = _latest_cached()
    seen, out = set(), []
    for key in ("items", "strong_pool", "tight_control_pool"):
        for item in payload.get(key) or []:
            c = str(item.get("code") or "").zfill(6)
            if c == code and c not in seen:
                seen.add(c)
                out.append(item)
    return out


@router.get("/{code}/kline")
async def chip_scanner_kline(code: str, days: int = Query(80, ge=20, le=250)):
    """详情副图：日线 OHLC（价格背景图）。"""
    try:
        return {"status": "ok", "code": code, **read_code_kline_with_series(code, days)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/{code}/distribution")
async def chip_scanner_distribution(code: str):
    """详情副图：筹码分布（本地 pytdx 分钟 K 近似分价，云端空 + local_only）。"""
    try:
        return {"status": "ok", "code": code, **read_code_distribution(code)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/{code}")
async def chip_scanner_code(code: str, date: str = Query(None)):
    try:
        if date is None:
            return {"status": "ok", "code": code, "records": _filter_code_from_snapshot(code)}
        return {"status": "ok", "code": code, "records": read_code_hits(code, date)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
