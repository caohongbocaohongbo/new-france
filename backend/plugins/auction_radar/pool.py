"""28 低位竞价雷达：读池/准备/冻结（仅 T-1 低位池，沪深主板）。"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime

from backend.plugins.common import read_snapshot
from backend.services.trading_calendar import prev_trading_day
from .config import CONFIG
from .models import FrozenPool, PoolSnapshot


def _finite(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _to_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _norm_items(items):
    seen = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        code = str(it.get("code") or "").strip().zfill(6)
        if len(code) != 6 or not code.isdigit():
            continue
        if code not in seen:
            seen[code] = dict(it)
            seen[code]["code"] = code
    return list(seen.values())


def hash_pool(items):
    codes = sorted(str(it.get("code") or "") for it in items)
    return hashlib.sha256(",".join(codes).encode("utf-8")).hexdigest()


def read_low_pool_snapshot(trade_day) -> PoolSnapshot:
    """纯读；校验状态/字段；无网络、无低位重算。"""
    payload = read_snapshot(CONFIG["low_snapshot_name"])
    if not payload:
        return PoolSnapshot(status="empty", reason="低位池快照不存在")
    if payload.get("status") != "completed":
        return PoolSnapshot(status="empty", reason=f"低位池状态={payload.get('status')}")
    items = _norm_items(payload.get("items"))
    low_items = [it for it in items if _finite(it.get("low_score")) is not None]
    if not low_items:
        return PoolSnapshot(status="no_candidate", reason="低位池无有效 low_score 候选", items=items)
    asof = str(payload.get("date") or payload.get("asof_trading_day") or "")
    return PoolSnapshot(
        status="completed",
        asof_trading_day=asof,
        items=low_items,
        pool_hash=hash_pool(low_items),
        total_count=len(low_items),
        producer_completed_at=str(payload.get("now") or ""),
    )


def prepare_low_pool(trade_day, allow_regenerate: bool = True) -> PoolSnapshot:
    """盘前/盘后后台作业；必要时触发插件 16。"""
    snap = read_low_pool_snapshot(trade_day)
    if snap.status == "completed":
        return snap
    if not allow_regenerate:
        return snap
    try:
        from backend.plugins.low_position_scanner.service import run_low_position_once
        import asyncio
        target = _to_date(trade_day) or date.today()
        asyncio.run(run_low_position_once(target_date=target, force=True))
    except Exception as exc:  # noqa: BLE001
        snap.reason = f"产池兜底失败: {exc}"
        return snap
    return read_low_pool_snapshot(trade_day)


def freeze_pool(trade_day) -> PoolSnapshot:
    """冻结 T-1 低位池；校验 asof_trading_day == prev_trading_day(trade_day)。"""
    snap = read_low_pool_snapshot(trade_day)
    if snap.status != "completed":
        return snap
    prev = prev_trading_day(_to_date(trade_day)) if _to_date(trade_day) else None
    expected = prev.isoformat() if prev else None
    if expected and snap.asof_trading_day != expected:
        snap.status = "stale"
        snap.reason = f"池 asof={snap.asof_trading_day} 但上一交易日={expected}"
    return snap
