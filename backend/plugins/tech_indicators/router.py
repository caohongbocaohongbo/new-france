"""18 经典技术指标选股 API。"""
import logging

from fastapi import APIRouter, Query

from backend.plugins.common import read_snapshot_resilient, snapshot_mem_get, snapshot_mem_set

from .config import SNAPSHOT_NAME
from .service import read_code_hits, read_code_kline_with_series

router = APIRouter()
logger = logging.getLogger(__name__)


def _latest_cached() -> dict:
    """G6：先查快照内存缓存（<1ms），无则本地文件优先、data-snapshots raw 兜底（22 方案读入口）。"""
    cached = snapshot_mem_get(SNAPSHOT_NAME)
    if cached is not None:
        return cached
    payload = read_snapshot_resilient(SNAPSHOT_NAME)
    snapshot_mem_set(SNAPSHOT_NAME, payload)
    return payload


@router.get("/latest")
async def tech_indicators_latest():
    try:
        return _latest_cached()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def _filter_code_from_snapshot(code: str) -> list:
    """G10：从快照内存 filter code（当日，不走 SQLite）。"""
    code = str(code).zfill(6)
    payload = _latest_cached()
    seen, out = set(), []
    for key in ("items", "golden_pool", "oversold_pool", "multi_hit_pool"):
        for item in payload.get(key) or []:
            c = str(item.get("code") or "").zfill(6)
            if c == code and c not in seen:
                seen.add(c)
                out.append(item)
    return out


@router.get("/{code}/kline")
async def tech_indicators_kline(code: str, days: int = Query(80, ge=20, le=250)):
    """详情副图：日线 OHLC + MACD/KDJ/RSI/BOLL/MA 全序列（复用 17 ECharts 模式）。"""
    try:
        return {"status": "ok", "code": code, **read_code_kline_with_series(code, days)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/{code}")
async def tech_indicators_code(code: str, date: str = Query(None)):
    try:
        if date is None:
            # G10：无 date 参数时，从当日快照内存 filter（不走 SQLite）
            return {"status": "ok", "code": code, "records": _filter_code_from_snapshot(code)}
        # 有 date 参数时，走 SQLite 历史时间线查询（本地历史）
        return {"status": "ok", "code": code, "records": read_code_hits(code, date)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
