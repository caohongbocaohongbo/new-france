"""18 经典技术指标选股编排服务。"""
import asyncio
import logging
from datetime import date, datetime
from typing import Callable, Optional

import pandas as pd

from backend.api.router_system import trading_session_status
from backend.plugins.common import (
    BEIJING_TZ, db_append, db_delete, db_query, float_or, get_kline_cached, intraday_append,
    json_safe, market_filter, read_code_kline, read_snapshot, write_snapshot,
)
from backend.plugins.multi_hit_notifier import push_multi_hit
from backend.plugins.principal_capital.sources.multi_source import fetch_market_fund_flow_resilient
from backend.services.trading_calendar import is_trading_day

from .config import CONFIG, SNAPSHOT_NAME
from .indicators import (
    boll_series_full, compute_boll, compute_kdj, compute_macd, compute_rsi, compute_tech_score,
    kdj_series_full, macd_series_full, ma_series_full, rsi_series_full,
)

logger = logging.getLogger(__name__)

# 多命中🔥邮件推送列（key, label）
MULTI_COLS = [
    ("name", "名称"), ("price", "价格"), ("hit_count", "命中数"), ("tech_score", "综合分"),
    ("macd_golden", "MACD金叉"), ("kdj_golden", "KDJ金叉"),
    ("rsi_oversold", "RSI超卖"), ("boll_rebound", "BOLL反弹"),
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


def evaluate_tech(code: str, name: str, price, closes, highs, lows, vols, cfg: dict = None,
                  is_intraday: bool = False, realtime_price=None, realtime_vol=None) -> Optional[dict]:
    """单票四大指标计算 + 命中分类 + 综合分 + multi_hit（盘中用实时价/量重算）。"""
    cfg = cfg or CONFIG
    if not closes or len(closes) < 30:
        return None
    rt = float_or(realtime_price) if is_intraday else None
    if rt is not None and rt > 0:
        # 盘中实时化：追加当日实时价/量（高=昨高与实时价取大，低取小，近似），复用 17 intraday 口径
        closes = intraday_append(closes, rt)
        highs = intraday_append(highs, max(highs[-1] if highs else rt, rt))
        lows = intraday_append(lows, min(lows[-1] if lows else rt, rt))
        vols = intraday_append(vols, float_or(realtime_vol))
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
        "is_intraday": int(is_intraday), "realtime_price": rt,
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


async def _scan(coarse: pd.DataFrame, kline_fetcher: Callable, workers: int, kline_days: int,
                is_intraday: bool = False) -> list:
    """并发拉 K 线并计算指标，Semaphore 限流。"""
    semaphore = asyncio.Semaphore(max(1, int(workers)))

    async def scan(row):
        async with semaphore:
            code = str(row.get("code", "")).zfill(6)
            try:
                # G5：共享 K 线缓存（18/19/20/21 复用，避免重复拉东财）
                hist = await asyncio.to_thread(get_kline_cached, code, kline_days, kline_fetcher)
                closes, highs, lows, vols = extract_ohlcv(hist)
            except Exception as exc:  # noqa: BLE001
                logger.debug("K线拉取失败 %s: %s", code, exc)
                closes = highs = lows = vols = None
            return evaluate_tech(code, str(row.get("name", "")), row.get("price"),
                                 closes, highs, lows, vols, is_intraday=is_intraday,
                                 realtime_price=row.get("price") if is_intraday else None,
                                 realtime_vol=row.get("vol") if is_intraday else None)

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


def read_code_kline_with_series(code: str, days: int = 80) -> dict:
    """详情副图：个股日线 OHLC + MACD/KDJ/RSI/BOLL/MA 全序列（复用 common.read_code_kline）。"""
    records = read_code_kline(code, days)
    if not records:
        return {"records": [], "series": {}}
    closes = [r.get("close") for r in records]
    highs = [r.get("high") for r in records]
    lows = [r.get("low") for r in records]
    return {
        "records": records,
        "series": {
            "ma": ma_series_full(closes),
            "macd": macd_series_full(closes),
            "kdj": kdj_series_full(closes, highs, lows),
            "rsi": rsi_series_full(closes),
            "boll": boll_series_full(closes),
        },
    }


async def run_tech_indicators_once(target_date=None, force: bool = False,
                                   fund_flow_fetcher=None, kline_fetcher=None,
                                   max_kline_workers: int = None, notifier=None) -> dict:
    """盘后/盘中执行一轮经典技术指标选股（盘中用实时价重算 + 多命中🔥邮件推送）。"""
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
    # 盘中实时化判定（复用 17 intraday 模式）
    session = trading_session_status(now)
    is_intraday = bool(session.get("is_trading_hours"))
    hits = await _scan(coarse, kline_fetcher, workers, int(CONFIG["kline_days"]), is_intraday=is_intraday)
    # G8：tech_score_pct（当日百分位排名 ×100，跨日可比）
    import bisect as _bisect
    sorted_scores = sorted(h["tech_score"] for h in hits)
    for h in hits:
        h["tech_score_pct"] = round(_bisect.bisect_right(sorted_scores, h["tech_score"]) / len(sorted_scores) * 100, 1) if sorted_scores else 0.0
    multi_pool = [h for h in hits if h["multi_hit"]]
    golden_pool = [h for h in hits if h["macd_golden"]]
    oversold_pool = [h for h in hits if h["kdj_golden"] or h["rsi_oversold"] or h["boll_rebound"]]
    # 多命中🔥邮件推送（新增票 vs 上一轮快照 + 30min 冷却去重）
    prev_snapshot = read_snapshot(SNAPSHOT_NAME) or {}
    prev_multi = {str(it.get("code")) for it in prev_snapshot.get("multi_hit_pool") or []}
    new_multi = [h for h in multi_pool if h["code"] not in prev_multi]
    email_sent, _notified, email_error = push_multi_hit(
        "tech", "经典技术指标", "多指标共振", new_multi, now,
        int(CONFIG["notify_cooldown_minutes"]), MULTI_COLS, notifier)
    # G9：快照顶层结构固定（status/run_at/signal_counts/golden_pool/oversold_pool/multi_hit_pool）
    payload = {
        "status": "completed",
        "run_at": now.isoformat(),
        "date": target.isoformat(),
        "active_source": (source_status or {}).get("active_source"),
        "count": len(hits),
        "signal_counts": {"multi": len(multi_pool), "golden": len(golden_pool), "oversold": len(oversold_pool)},
        "golden_pool": golden_pool[:40],
        "oversold_pool": oversold_pool[:40],
        "multi_hit_pool": multi_pool[:40],
        "items": hits[:80],
        "is_intraday": is_intraday,
        "email_sent": bool(email_sent), "email_error": email_error,
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
