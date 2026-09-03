"""18 经典技术指标选股编排服务。"""
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
    compute_boll, compute_kdj, compute_macd, compute_rsi, compute_tech_score,
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
    """粗筛：市场过滤(默认主板) + 成交额下限。"""
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


def evaluate_tech(code: str, name: str, price, closes, highs, lows, vols, cfg: dict = None) -> Optional[dict]:
    """单票四大指标计算 + 命中分类 + 综合分 + multi_hit。"""
    cfg = cfg or CONFIG
    if not closes or len(closes) < 30:
        return None
    macd = compute_macd(closes)
    kdj = compute_kdj(closes, highs, lows, low_threshold=cfg["kdj_low_threshold"])
    rsi = compute_rsi(closes, oversold=cfg["rsi_oversold"])
    boll = compute_boll(closes)
    macd_golden = bool(macd and macd.get("golden"))
    kdj_golden = bool(kdj and kdj.get("low_golden"))
    rsi_oversold = bool(rsi and rsi.get("oversold_rebound"))
    boll_rebound = bool(boll and boll.get("rebound"))
    hits = [macd_golden, kdj_golden, rsi_oversold, boll_rebound]
    hit_count = sum(hits)
    if hit_count == 0:
        return None  # 无命中不入选
    score = compute_tech_score(hit_count, macd, kdj, rsi, boll)
    return {
        "code": str(code).zfill(6), "name": name, "price": float_or(price),
        "macd_golden": int(macd_golden), "kdj_golden": int(kdj_golden),
        "rsi_oversold": int(rsi_oversold), "boll_rebound": int(boll_rebound),
        "hit_count": hit_count, "tech_score": score, "multi_hit": 1 if hit_count >= 2 else 0,
        "macd_dif": macd.get("dif") if macd else None, "macd_dea": macd.get("dea") if macd else None,
        "kdj_k": kdj.get("k") if kdj else None, "kdj_d": kdj.get("d") if kdj else None,
        "kdj_j": kdj.get("j") if kdj else None,
        "rsi": rsi.get("rsi") if rsi else None,
        "boll_mb": boll.get("mb") if boll else None,
        "boll_ub": boll.get("ub") if boll else None, "boll_lb": boll.get("lb") if boll else None,
    }


async def _scan(coarse: pd.DataFrame, kline_fetcher: Callable, workers: int, kline_days: int) -> list:
    """并发拉 K 线并计算指标，Semaphore 限流。"""
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
            return evaluate_tech(code, str(row.get("name", "")), row.get("price"), closes, highs, lows, vols)

    results = await asyncio.gather(*(scan(row) for _, row in coarse.iterrows()), return_exceptions=True)
    hits = [r for r in results if isinstance(r, dict)]
    hits.sort(key=lambda r: (r["multi_hit"], r["tech_score"]), reverse=True)
    return hits


def read_latest() -> dict:
    return read_snapshot(SNAPSHOT_NAME) or {"status": "empty", "items": []}


def read_code_hits(code: str, date_str: str = None) -> list:
    sql = "SELECT * FROM tech_indicator_hits WHERE code = :c"
    params = {"c": str(code).zfill(6)}
    if date_str:
        sql += " AND date = :d"; params["d"] = date_str
    sql += " ORDER BY date DESC"
    df = db_query(sql, params)
    return json_safe(df.to_dict("records")) if not df.empty else []


async def run_tech_indicators_once(target_date=None, force: bool = False,
                                   fund_flow_fetcher=None, kline_fetcher=None,
                                   max_kline_workers: int = None) -> dict:
    """盘后执行一轮经典技术指标选股。"""
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
    hits = await _scan(coarse, kline_fetcher, workers, int(CONFIG["kline_days"]))
    multi_pool = [h for h in hits if h["multi_hit"]]
    golden_pool = [h for h in hits if h["macd_golden"]]
    oversold_pool = [h for h in hits if h["kdj_golden"] or h["rsi_oversold"] or h["boll_rebound"]]
    payload = {
        "status": "completed", "date": target.isoformat(), "now": now.isoformat(),
        "active_source": (source_status or {}).get("active_source"),
        "count": len(hits),
        "signal_counts": {"multi": len(multi_pool), "golden": len(golden_pool), "oversold": len(oversold_pool)},
        "items": hits[:80],
        "disclaimer": "技术指标为滞后指标，仅为辅助参考，不构成投资建议",
    }
    write_snapshot(SNAPSHOT_NAME, payload)
    if hits:
        db_delete("tech_indicator_hits", {"date": target.isoformat()})
        db_append("tech_indicator_hits", [
            {"date": target.isoformat(), **{k: h.get(k) for k in (
                "code", "name", "price", "macd_golden", "kdj_golden", "rsi_oversold", "boll_rebound",
                "hit_count", "tech_score", "multi_hit", "macd_dif", "macd_dea",
                "kdj_k", "kdj_d", "kdj_j", "rsi", "boll_mb", "boll_ub", "boll_lb")}}
            for h in hits
        ])
    return payload
