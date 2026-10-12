"""28 低位竞价雷达 API：纯读 + 受控触发器。"""
from __future__ import annotations

import json
import logging

import hmac

from fastapi import APIRouter, Header, HTTPException, Query

from .config import CONFIG
from .pool import read_low_pool_snapshot
from .service import read_latest, run_auction_radar_once

router = APIRouter()
logger = logging.getLogger(__name__)


def _error(message):
    return {"status": "error", "message": str(message), "items": []}


@router.get("/latest")
async def latest():
    try:
        return read_latest()
    except Exception as exc:  # noqa: BLE001
        logger.exception("读取 latest 失败")
        return _error(exc)


@router.get("/preliminary")
async def preliminary():
    from .config import PRELIMINARY_FILE
    if not PRELIMINARY_FILE.exists():
        return {"status": "no_data", "items": []}
    try:
        return json.loads(PRELIMINARY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "error", "items": []}


@router.get("/pool")
async def pool():
    from datetime import datetime
    from .config import BEIJING_TZ
    snap = read_low_pool_snapshot(datetime.now(BEIJING_TZ).date())
    return {"status": snap.status, "reason": snap.reason, "asof_trading_day": snap.asof_trading_day,
            "pool_hash": snap.pool_hash, "total_count": snap.total_count, "items": snap.items}


@router.get("/review")
async def review():
    from backend.plugins.common import db_query
    try:
        df = db_query("SELECT trade_date, COUNT(*) AS n FROM auction_candidates GROUP BY trade_date ORDER BY trade_date DESC LIMIT 30")
        return {"status": "ok", "rows": df.to_dict("records") if not df.empty else []}
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@router.get("/candidates")
async def candidates():
    """历史候选列表（含行情字段 + 加入时间 + 告警时间），供前端一次加载后本地筛选。"""
    from backend.plugins.common import db_query
    try:
        df = db_query(
            "SELECT c.code, c.name, c.price, c.last_close, c.gap, c.volume_ratio, c.turnover, "
            "c.auction_score, c.total_score, c.source_timestamp AS trade_time, "
            "r.trade_date AS added_date, r.published_at AS alert_time "
            "FROM auction_candidates c JOIN auction_runs r ON c.run_id = r.id "
            "ORDER BY r.trade_date DESC, c.rank ASC LIMIT 2000"
        )
        return {"status": "ok", "count": len(df) if not df.empty else 0,
                "items": df.to_dict("records") if not df.empty else []}
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@router.get("/stocks/{code}")
async def stock(code: str):
    from backend.plugins.common import db_query
    try:
        df = db_query("SELECT * FROM auction_candidates WHERE code=:c ORDER BY id DESC LIMIT 20", {"c": code.zfill(6)})
        return {"status": "ok", "code": code.zfill(6), "records": df.to_dict("records") if not df.empty else []}
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@router.post("/trigger")
async def trigger(dry_run: bool = Query(True), force: bool = Query(False),
                  authorization: str = Header(None), x_auction_token: str = Header(None)):
    """受控触发器：默认关闭；启用时必须校验 Bearer 或 X-Auction-Token（常量时间比较）。"""
    if not CONFIG.get("trigger_enabled"):
        raise HTTPException(status_code=404, detail="trigger 未启用")
    expected = CONFIG.get("trigger_token", "")
    if not expected:
        raise HTTPException(status_code=403, detail="未配置 trigger token")
    provided = x_auction_token
    if not provided and authorization and authorization.startswith("Bearer "):
        provided = authorization[7:]
    if not provided:
        raise HTTPException(status_code=401, detail="缺少 token")
    if not hmac.compare_digest(str(provided), str(expected)):
        raise HTTPException(status_code=403, detail="token 错误")
    try:
        return await run_auction_radar_once(dry_run=dry_run, force=force)
    except Exception as exc:  # noqa: BLE001
        logger.exception("trigger 执行失败")
        raise HTTPException(status_code=500, detail=str(exc))
