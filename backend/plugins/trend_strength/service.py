"""19 趋势强度选股编排服务。"""
import asyncio
import logging
from datetime import date, datetime
from typing import Callable, Optional

import pandas as pd

from backend.plugins.common import (
    BEIJING_TZ, db_append, db_delete, db_query, float_or, json_safe, market_filter,
    read_snapshot, write_snapshot,
)
from backend.plugins.principal_capital.sources.multi_source import fetch_market_fund_flow_resilient
from backend.services.trading_calendar import is_trading_day

from .config import CONFIG, SNAPSHOT_NAME
from .indicators import (
    compute_trend_score, is_ma_aligned, is_new_high, ma_values, volume_ratio,
)

logger = logging.getLogger(__name__)


def _to_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return datetime.now(BEIJING_TZ).date()


def _coarse_filter(df: pd.DataFrame, cfg: dict = None) -> pd.DataFrame:
    cfg = cfg or CONFIG
    if df is None or df.empty:
        return pd.DataFrame()
    filtered = market_filter(df, show_gem=cfg.get("show_gem", False),
                             show_star=cfg.get("show_star", False),
                             min_amount=float(cfg["min_amount"]))
    return filtered.reset_index(drop=True)


def extract_ohlcv(hist):
    if hist is None or getattr(hist, "empty", True):
        return None, None, None, None
    def col(*names):
        for n in names:
            if n in hist.columns:
                return hist[n].tolist()
        return None
    return (col("收盘", "close"), col("最高", "high"), col("最低", "low"), col("成交量", "vol", "volume"))


def evaluate_trend(code: str, name: str, price, closes, highs, lows, vols, cfg: dict = None) -> Optional[dict]:
    """单票 MA 多头 + 创新高 + 趋势强度分。"""
    cfg = cfg or CONFIG
    if not closes or len(closes) < int(cfg["new_high_window"]) + 1:
        return None
    aligned = is_ma_aligned(closes, cfg["ma_tol"])
    new_high, prev_high = is_new_high(closes, int(cfg["new_high_window"]))
    if not (aligned and new_high):
        return None  # AND 条件，两条全过才入选
    vr = volume_ratio(vols) if vols else None
    leader = aligned and new_high and vr is not None and vr > float(cfg["leader_volume_ratio"])
    ma = ma_values(closes)
    ma60 = ma["ma60"]
    score = compute_trend_score(closes, vols, prev_high, ma60, vr)
    high_break = round((closes[-1] - prev_high) / prev_high, 4) if prev_high else None
    return {
        "code": str(code).zfill(6), "name": name, "price": float_or(price),
        "ma5": ma["ma5"], "ma10": ma["ma10"], "ma20": ma["ma20"], "ma60": ma60,
        "ma_aligned": int(aligned), "prev_high_60": prev_high, "new_high": int(new_high),
        "high_break_pct": high_break, "volume_ratio": vr,
        "trend_leader": int(leader), "trend_score": score,
    }


async def _scan(coarse: pd.DataFrame, kline_fetcher: Callable, workers: int, kline_days: int, cfg: dict = None) -> list:
    """并发拉 K 线并计算趋势强度，Semaphore 限流。"""
    cfg = cfg or CONFIG
    semaphore = asyncio.Semaphore(max(1, int(workers)))

    async def scan(row):
        async with semaphore:
            code = str(row.get("code", "")).zfill(6)
            try:
                hist = await asyncio.to_thread(kline_fetcher, code, kline_days)
                closes, highs, lows, vols = extract_ohlcv(hist)
            except Exception as exc:  # noqa: BLE001
                logger.debug("K线拉取失败 %s: %s", code, exc)
                closes = highs = lows = vols = None
            return evaluate_trend(code, str(row.get("name", "")), row.get("price"), closes, highs, lows, vols, cfg)

    results = await asyncio.gather(*(scan(row) for _, row in coarse.iterrows()), return_exceptions=True)
    hits = [r for r in results if isinstance(r, dict)]
    hits.sort(key=lambda r: (r["trend_leader"], r["trend_score"]), reverse=True)
    return hits


def read_latest() -> dict:
    return read_snapshot(SNAPSHOT_NAME) or {"status": "empty", "items": []}


def read_code_hits(code: str, date_str: str = None) -> list:
    sql = "SELECT * FROM trend_strength_hits WHERE code = :c"
    params = {"c": str(code).zfill(6)}
    if date_str:
        sql += " AND date = :d"; params["d"] = date_str
    sql += " ORDER BY date DESC"
    df = db_query(sql, params)
    return json_safe(df.to_dict("records")) if not df.empty else []


async def run_trend_strength_once(target_date=None, force: bool = False,
                                  fund_flow_fetcher=None, kline_fetcher=None,
                                  max_kline_workers: int = None) -> dict:
    """盘后执行一轮趋势强度选股。"""
    now = datetime.now(BEIJING_TZ)
    target = _to_date(target_date) if target_date is not None else now.date()
    if not force and not is_trading_day(target):
        payload = {"status": "skipped", "reason": "非交易日", "date": target.isoformat(), "items": []}
        write_snapshot(SNAPSHOT_NAME, payload)
        return payload
    df, source_status = (fund_flow_fetcher or fetch_market_fund_flow_resilient)()
    if df is None or df.empty:
        payload = {"status": "no_data", "reason": "资金流为空", "date": target.isoformat(), "items": []}
        write_snapshot(SNAPSHOT_NAME, payload)
        return payload
    coarse = _coarse_filter(df)
    if coarse.empty:
        payload = {"status": "no_data", "reason": "粗筛后无候选", "date": target.isoformat(), "items": []}
        write_snapshot(SNAPSHOT_NAME, payload)
        return payload
    workers = int(max_kline_workers or CONFIG["kline_workers"])
    if kline_fetcher is None:
        from backend.agents.layer1_data_collector.sources.historical_kline import fetch_historical as kline_fetcher
    hits = await _scan(coarse, kline_fetcher, workers, int(CONFIG["kline_days"]), CONFIG)
    leader_pool = [h for h in hits if h["trend_leader"]]
    payload = {
        "status": "completed", "date": target.isoformat(), "now": now.isoformat(),
        "active_source": (source_status or {}).get("active_source"),
        "count": len(hits),
        "signal_counts": {"leader": len(leader_pool)},
        "items": hits[:80],
        "disclaimer": "趋势追高有回撤风险，仅为辅助参考，不构成投资建议",
    }
    write_snapshot(SNAPSHOT_NAME, payload)
    if hits:
        db_delete("trend_strength_hits", {"date": target.isoformat()})
        db_append("trend_strength_hits", [
            {"date": target.isoformat(), **{k: h.get(k) for k in (
                "code", "name", "price", "ma5", "ma10", "ma20", "ma60",
                "ma_aligned", "prev_high_60", "new_high", "high_break_pct",
                "volume_ratio", "trend_leader", "trend_score")}}
            for h in hits
        ])
    return payload
