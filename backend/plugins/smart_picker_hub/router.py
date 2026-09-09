"""22 智能选股聚合中枢 API。"""
import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query

from .config import POOL_KEYS, SORT_WHITELIST
from .indicators import zcode
from .service import build_chart_payload, query_code, query_history, query_latest, query_meta, read_perf

router = APIRouter()
logger = logging.getLogger(__name__)


def _validate(sort: str, pool: str, min_hit: int, limit: int, offset: int, order: str, market: str) -> None:
    """参数白名单校验，非法值 422（不静默兜底）。"""
    if sort not in SORT_WHITELIST:
        raise HTTPException(status_code=422, detail="sort 必须在白名单内: " + ",".join(SORT_WHITELIST))
    if pool not in ("all",) + POOL_KEYS:
        raise HTTPException(status_code=422, detail="pool 必须在: all," + ",".join(POOL_KEYS))
    if not 1 <= min_hit <= 4:
        raise HTTPException(status_code=422, detail="min_hit 必须在 1-4")
    if not 1 <= limit <= 200:
        raise HTTPException(status_code=422, detail="limit 必须在 1-200")
    if not 0 <= offset <= 10000:
        raise HTTPException(status_code=422, detail="offset 必须在 0-10000")
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=422, detail="order 必须为 asc/desc")
    if market not in ("main", "gem", "star"):
        raise HTTPException(status_code=422, detail="market 必须为 main/gem/star")


@router.get("/latest")
async def smart_picker_latest(
    q: str = Query("", max_length=32),
    pool: str = Query("all"),
    min_hit: int = Query(1, ge=1, le=4),
    sort: str = Query("hub_score"),
    order: str = Query("desc"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10000),
    market: str = Query("main"),
    date: str = Query(None),
):
    """聚合总榜：统一分 + 共振 + 服务端搜索/筛选/排序/分页（快照内存 <1ms）。"""
    try:
        _validate(sort, pool, min_hit, limit, offset, order, market)
        if date is not None:
            return query_history(str(date)[:10], q, pool, min_hit, market, sort, order, limit, offset)
        return query_latest(q, pool, min_hit, market, sort, order, limit, offset)
    except HTTPException:
        raise
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/perf")
async def smart_picker_perf(days: int = Query(20, ge=1, le=120), window: str = Query("t1")):
    """信号质量卡：近 N 交易日 T+1/T+3/T+5 收益汇总。"""
    try:
        return read_perf(days, window)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/meta")
async def smart_picker_meta():
    """元信息：权重/策略可用性/门控/图表/绩效配置。"""
    try:
        return query_meta()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/{code}/chart")
async def smart_picker_chart(code: str, days: int = Query(80, ge=20, le=250)):
    """个股图表：快照预计算优先（cached:true），否则当日缓存拉取重算（cached:false）。"""
    from .service import _charts_cached

    try:
        hit = (_charts_cached().get("charts") or {}).get(zcode(code))
        if hit:
            return {"status": "ok", "code": zcode(code), "cached": True, **hit}
        payload = await asyncio.to_thread(build_chart_payload, zcode(code), days, None)
        if not payload.get("records"):
            return {"status": "no_data", "code": zcode(code), "reason": "kline_unavailable", "cached": False}
        return {"status": "ok", "code": zcode(code), "cached": False, **payload}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/{code}")
async def smart_picker_code(code: str, date: str = Query(None)):
    """个股当日命中详情（快照内存 filter）；带 date 走本地 SQLite 历史。"""
    try:
        return query_code(code, date)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
