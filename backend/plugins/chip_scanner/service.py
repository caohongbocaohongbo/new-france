"""21 筹码集中度与获利盘选股编排服务（本地专属：pytdx 分钟 K 近似分价）。"""
import asyncio
import logging
from datetime import date, datetime
from typing import Callable, Optional

import pandas as pd

from backend.plugins.common import (
    BEIJING_TZ, db_append, db_delete, db_query, float_or, get_kline_cached, json_safe,
    market_filter, read_code_kline, read_snapshot, write_snapshot,
)
from backend.plugins.multi_hit_notifier import push_multi_hit
from backend.plugins.principal_capital.sources.multi_source import fetch_market_fund_flow_resilient
from backend.plugins.volume_profile.indicators import (
    build_price_distribution, concentration_ratio, main_cost_band, profit_ratio,
)
from backend.services.trading_calendar import is_trading_day

from .config import CONFIG, SNAPSHOT_NAME
from .indicators import compute_chip_score, is_chip_hit, is_tight_control, ma20_slope

logger = logging.getLogger(__name__)

# 高度控盘🔥邮件推送列（key, label）
MULTI_COLS = [
    ("name", "名称"), ("price", "价格"), ("concentration_ratio", "集中度"),
    ("profit_ratio", "获利盘"), ("chip_score", "筹码分"),
]


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


def extract_closes(hist):
    if hist is None or getattr(hist, "empty", True):
        return None
    for col in ("收盘", "close"):
        if col in hist.columns:
            return [float_or(v) for v in hist[col].tolist()]
    return None


def evaluate_chip(code: str, name: str, price, bars: list, closes: list, cfg: dict = None) -> Optional[dict]:
    """单票筹码集中度 + 获利盘 + MA20 上行趋势确认。"""
    cfg = cfg or CONFIG
    if not bars or not closes or len(closes) < 26:
        return None
    # 分价分布（分钟 K 近似，volume_profile 的 allocate_bar 模式）
    distribution = build_price_distribution(bars)
    if not distribution:
        return None
    cr, p05, p50, p95 = concentration_ratio(distribution)
    profit = profit_ratio(distribution, price)
    slope = ma20_slope(closes)
    trend_up = slope is not None and slope > 0
    hit = is_chip_hit(cr, profit, trend_up, cfg["concentration_max"], cfg["profit_min"])
    if not hit:
        return None
    tight = is_tight_control(cr, profit, cfg["tight_concentration_max"], cfg["tight_profit_min"])
    band = main_cost_band(distribution)
    score = compute_chip_score(cr, profit, slope)
    return {
        "code": str(code).zfill(6), "name": name, "price": float_or(price),
        "concentration_ratio": cr, "p05": p05, "p50": p50, "p95": p95,
        "profit_ratio": profit, "ma20_slope": slope,
        "concentrated": int(cr is not None and cr < cfg["concentration_max"]),
        "high_profit": int(profit is not None and profit > cfg["profit_min"]),
        "trend_up": int(trend_up), "tight_control": int(tight),
        "main_cost_band_low": band.get("low"), "main_cost_band_high": band.get("high"),
        "chip_score": score, "approx": True,  # 分钟 K 近似分价，非真实逐笔分价
    }


async def _scan(coarse: pd.DataFrame, bars_fetcher: Callable, kline_fetcher: Callable,
                workers: int, kline_days: int, cfg: dict = None) -> list:
    """并发拉分钟 K 线并计算筹码指标，Semaphore 限流（G5：日线走共享缓存）。"""
    cfg = cfg or CONFIG
    semaphore = asyncio.Semaphore(max(1, int(workers)))

    async def scan(row):
        async with semaphore:
            code = str(row.get("code", "")).zfill(6)
            try:
                bars = await asyncio.to_thread(bars_fetcher, code)
                closes_hist = await asyncio.to_thread(get_kline_cached, code, kline_days, kline_fetcher)
                closes = extract_closes(closes_hist)
            except Exception as exc:  # noqa: BLE001
                logger.debug("K线拉取失败 %s: %s", code, exc)
                bars, closes = None, None
            return evaluate_chip(code, str(row.get("name", "")), row.get("price"), bars, closes, cfg)

    results = await asyncio.gather(*(scan(row) for _, row in coarse.iterrows()), return_exceptions=True)
    hits = [r for r in results if isinstance(r, dict)]
    hits.sort(key=lambda r: (r["tight_control"], r["chip_score"]), reverse=True)
    return hits


def read_latest() -> dict:
    return read_snapshot(SNAPSHOT_NAME) or {"status": "empty", "items": []}


def read_code_hits(code: str, date_str: str = None) -> list:
    sql = "SELECT * FROM chip_hits WHERE code = :c"
    params = {"c": str(code).zfill(6)}
    if date_str:
        sql += " AND date = :d"; params["d"] = date_str
    sql += " ORDER BY date DESC"
    df = db_query(sql, params)
    return json_safe(df.to_dict("records")) if not df.empty else []


def _default_bars_fetcher(code: str) -> list:
    """本地 pytdx 分钟 K（1 分钟 bar，本地专属，云端不可用）。"""
    try:
        from backend.plugins.smart_money_radar.sources.tdx_source import TdxPool, market_of
        pool = TdxPool()
        try:
            return pool.fetch_bars(market_of(code), code, 8, 240)  # 1 分钟 bar，近 240 根（当日）
        finally:
            pool.disconnect()
    except Exception:  # noqa: BLE001
        return []


def read_code_kline_with_series(code: str, days: int = 80) -> dict:
    """详情副图：个股日线 OHLC（价格背景图，复用 common.read_code_kline）。"""
    return {"records": read_code_kline(code, days)}


def read_code_distribution(code: str) -> dict:
    """详情副图：筹码分布（本地 pytdx 分钟 K 近似分价，云端返回空 + local_only）。"""
    try:
        bars = _default_bars_fetcher(str(code).zfill(6))
    except Exception:  # noqa: BLE001
        bars = []
    distribution = build_price_distribution(bars) if bars else []
    return {
        "distribution": json_safe(distribution),
        "local_only": bool(CONFIG.get("local_only", True)),
        "approx": True,
    }


async def run_chip_scanner_once(target_date=None, force: bool = False,
                                fund_flow_fetcher=None, bars_fetcher=None, kline_fetcher=None,
                                max_kline_workers: int = None, notifier=None) -> dict:
    """盘后执行一轮筹码集中度选股（本地专属：pytdx 分钟 K 近似分价 + 高度控盘🔥邮件推送）。"""
    now = datetime.now(BEIJING_TZ)
    target = _to_date(target_date) if target_date is not None else now.date()
    if not force and not is_trading_day(target):
        payload = {"status": "skipped", "reason": "非交易日", "date": target.isoformat(), "items": []}
        write_snapshot(SNAPSHOT_NAME, payload)
        return payload
    bars_fetcher = bars_fetcher or _default_bars_fetcher
    # G1：东财分价接口不可用（已验证 stock_cyq_em 失败）→ 本地专属（pytdx 分钟 K）
    # 云端 Render 上 pytdx 不可用，bars_fetcher 返回空 → no_data（标"需本地运行"）
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
    hits = await _scan(coarse, bars_fetcher, kline_fetcher, workers, int(CONFIG["kline_days"]), CONFIG)
    if not hits:
        # 云端无分钟 K（pytdx 不可用）→ no_data，标"需本地运行"
        payload = {
            "status": "no_data", "reason": "需本地运行（pytdx 分钟 K 近似分价），云端分价接口不可用",
            "local_only": True, "approx": True,
            "date": target.isoformat(), "items": [],
        }
        write_snapshot(SNAPSHOT_NAME, payload)
        return payload
    # 百分位排名（跨日可比）
    import bisect as _bisect
    sorted_scores = sorted(h["chip_score"] for h in hits)
    for h in hits:
        h["chip_score_pct"] = round(_bisect.bisect_right(sorted_scores, h["chip_score"]) / len(sorted_scores) * 100, 1) if sorted_scores else 0.0
    strong_pool = [h for h in hits if h["concentrated"] and h["high_profit"] and h["trend_up"]]
    tight_pool = [h for h in hits if h["tight_control"]]
    # 高度控盘🔥邮件推送（新增票 vs 上一轮快照 + 30min 冷却去重）
    prev_snapshot = read_snapshot(SNAPSHOT_NAME) or {}
    prev_tight = {str(it.get("code")) for it in prev_snapshot.get("tight_control_pool") or []}
    new_tight = [h for h in tight_pool if h["code"] not in prev_tight]
    email_sent, _notified, email_error = push_multi_hit(
        "chip", "筹码集中度", "高度控盘", new_tight, now,
        int(CONFIG["notify_cooldown_minutes"]), MULTI_COLS, notifier)
    payload = {
        "status": "completed",
        "run_at": now.isoformat(),
        "date": target.isoformat(),
        "active_source": (source_status or {}).get("active_source"),
        "approx": True, "local_only": bool(CONFIG.get("local_only", True)),
        "count": len(hits),
        "signal_counts": {"strong": len(strong_pool), "tight_control": len(tight_pool)},
        "strong_pool": strong_pool[:40],
        "tight_control_pool": tight_pool[:40],
        "items": hits[:80],
        "email_sent": bool(email_sent), "email_error": email_error,
        "disclaimer": "分钟 K 近似分价，非真实逐笔分价；仅为辅助参考，不构成投资建议",
    }
    write_snapshot(SNAPSHOT_NAME, payload)
    if hits:
        # G6：写入前清理当日，防 cron 重跑重复写入
        db_delete("chip_hits", {"date": target.isoformat()})
        db_append("chip_hits", [
            {"date": target.isoformat(), **{k: h.get(k) for k in (
                "code", "name", "price", "concentration_ratio", "p05", "p50", "p95",
                "profit_ratio", "ma20_slope", "concentrated", "high_profit",
                "trend_up", "tight_control", "chip_score")}}
            for h in hits
        ])
    return payload
