"""20 形态突破选股编排服务。"""
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
    compute_pattern_score, detect_gap_hold, is_narrow_platform, is_platform_breakout,
)

logger = logging.getLogger(__name__)

# 双形态共振🔥邮件推送列（key, label）
MULTI_COLS = [
    ("name", "名称"), ("price", "价格"), ("gap_type", "缺口类型"),
    ("volume_ratio", "量比"), ("pattern_score", "形态分"),
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


def extract_ohlcv(hist):
    if hist is None or getattr(hist, "empty", True):
        return None, None, None, None, None
    def col(*names):
        for n in names:
            if n in hist.columns:
                return hist[n].tolist()
        return None
    return (col("开盘", "open"), col("收盘", "close"), col("最高", "high"), col("最低", "low"), col("成交量", "vol", "volume"))


def evaluate_pattern(code: str, name: str, price, opens, closes, highs, lows, vols, cfg: dict = None,
                     is_intraday: bool = False, realtime_price=None, realtime_vol=None) -> Optional[dict]:
    """单票平台突破 + 缺口不回补 + 双形态共振（盘中用实时价/量重算）。"""
    cfg = cfg or CONFIG
    if not closes or len(closes) < 60:
        return None
    rt = float_or(realtime_price) if is_intraday else None
    if rt is not None and rt > 0:
        # 盘中实时化：追加当日实时价/量（今日开盘价用昨收近似补齐序列长度，
        # 缺口 3 日前判定读取的是完整K线，不读追加的开盘值）。
        closes = intraday_append(closes, rt)
        highs = intraday_append(highs, max(highs[-1] if highs else rt, rt))
        lows = intraday_append(lows, min(lows[-1] if lows else rt, rt))
        vols = intraday_append(vols, float_or(realtime_vol))
        opens = intraday_append(opens, opens[-1] if opens else rt)
    narrow, platform_high = is_narrow_platform(highs, lows, int(cfg["platform_days"]),
                                               float(cfg["abs_threshold"]), float(cfg["converge_threshold"]))
    breakout, vr = is_platform_breakout(closes, vols, platform_high, float(cfg["breakout_vol_ratio"]))
    platform_break = narrow and breakout
    gap = detect_gap_hold(opens, highs, lows, vols, platform_high,
                          int(cfg["gap_lookback"]), float(cfg["min_gap_pct"]))
    gap_hold = bool(gap and gap.get("hold"))
    if not (platform_break or gap_hold):
        return None  # 无命中不入选
    dual_hit = platform_break and gap_hold
    prev_high = max(closes[-21:-1]) if len(closes) >= 21 else None
    break_pct = round((closes[-1] - prev_high) / prev_high, 4) if prev_high else None
    score = compute_pattern_score(platform_break, gap_hold, dual_hit, break_pct, (gap or {}).get("gap_pct"))
    return {
        "code": str(code).zfill(6), "name": name, "price": float_or(price),
        "is_intraday": int(is_intraday), "realtime_price": rt,
        "platform_high": platform_high, "break_pct": break_pct,
        "platform_break": int(platform_break), "gap_hold": int(gap_hold),
        "dual_hit": int(dual_hit),
        "gap_pct": (gap or {}).get("gap_pct"), "gap_type": (gap or {}).get("gap_type"),
        "gap_low": (gap or {}).get("gap_low"),
        "volume_ratio": vr if vr else (gap or {}).get("volume_ratio"),
        "pattern_score": score,
    }


async def _scan(coarse: pd.DataFrame, kline_fetcher: Callable, workers: int, kline_days: int,
                cfg: dict = None, is_intraday: bool = False) -> list:
    """并发拉 K 线并计算形态，Semaphore 限流（G4：共享 K 线缓存）。"""
    cfg = cfg or CONFIG
    semaphore = asyncio.Semaphore(max(1, int(workers)))

    async def scan(row):
        async with semaphore:
            code = str(row.get("code", "")).zfill(6)
            try:
                hist = await asyncio.to_thread(get_kline_cached, code, kline_days, kline_fetcher)
                opens, closes, highs, lows, vols = extract_ohlcv(hist)
            except Exception as exc:  # noqa: BLE001
                logger.debug("K线拉取失败 %s: %s", code, exc)
                opens = closes = highs = lows = vols = None
            return evaluate_pattern(code, str(row.get("name", "")), row.get("price"),
                                    opens, closes, highs, lows, vols, cfg, is_intraday=is_intraday,
                                    realtime_price=row.get("price") if is_intraday else None,
                                    realtime_vol=row.get("vol") if is_intraday else None)

    results = await asyncio.gather(*(scan(row) for _, row in coarse.iterrows()), return_exceptions=True)
    hits = [r for r in results if isinstance(r, dict)]
    hits.sort(key=lambda r: (r["dual_hit"], r["pattern_score"]), reverse=True)
    return hits


def read_latest() -> dict:
    return read_snapshot(SNAPSHOT_NAME) or {"status": "empty", "items": []}


def read_code_hits(code: str, date_str: str = None) -> list:
    sql = "SELECT * FROM pattern_hits WHERE code = :c"
    params = {"c": str(code).zfill(6)}
    if date_str:
        sql += " AND date = :d"; params["d"] = date_str
    sql += " ORDER BY date DESC"
    df = db_query(sql, params)
    return json_safe(df.to_dict("records")) if not df.empty else []


def read_code_kline_with_series(code: str, days: int = 80) -> dict:
    """详情副图：个股日线 OHLC（平台突破/缺口标记由前端用命中行字段叠加，复用 common.read_code_kline）。"""
    return {"records": read_code_kline(code, days)}


async def run_pattern_scanner_once(target_date=None, force: bool = False,
                                   fund_flow_fetcher=None, kline_fetcher=None,
                                   max_kline_workers: int = None, notifier=None) -> dict:
    """盘后/盘中执行一轮形态突破选股（盘中用实时价重算 + 双形态共振🔥邮件推送）。"""
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
    hits = await _scan(coarse, kline_fetcher, workers, int(CONFIG["kline_days"]), CONFIG, is_intraday=is_intraday)
    # 百分位排名（跨日可比）
    import bisect as _bisect
    sorted_scores = sorted(h["pattern_score"] for h in hits)
    for h in hits:
        h["pattern_score_pct"] = round(_bisect.bisect_right(sorted_scores, h["pattern_score"]) / len(sorted_scores) * 100, 1) if sorted_scores else 0.0
    platform_pool = [h for h in hits if h["platform_break"]]
    gap_pool = [h for h in hits if h["gap_hold"]]
    dual_pool = [h for h in hits if h["dual_hit"]]
    # 双形态共振🔥邮件推送（新增票 vs 上一轮快照 + 30min 冷却去重）
    prev_snapshot = read_snapshot(SNAPSHOT_NAME) or {}
    prev_dual = {str(it.get("code")) for it in prev_snapshot.get("dual_pool") or []}
    new_dual = [h for h in dual_pool if h["code"] not in prev_dual]
    email_sent, _notified, email_error = push_multi_hit(
        "pattern", "形态突破", "双形态共振", new_dual, now,
        int(CONFIG["notify_cooldown_minutes"]), MULTI_COLS, notifier)
    # G7：快照顶层结构固定（status/run_at/signal_counts/platform_pool/gap_pool/dual_pool）
    payload = {
        "status": "completed",
        "run_at": now.isoformat(),
        "date": target.isoformat(),
        "active_source": (source_status or {}).get("active_source"),
        "count": len(hits),
        "signal_counts": {"platform": len(platform_pool), "gap": len(gap_pool), "dual": len(dual_pool)},
        "platform_pool": platform_pool[:40],
        "gap_pool": gap_pool[:40],
        "dual_pool": dual_pool[:40],
        "items": hits[:80],
        "is_intraday": is_intraday,
        "email_sent": bool(email_sent), "email_error": email_error,
        "disclaimer": "形态突破可能是假突破，仅为辅助参考，不构成投资建议",
    }
    write_snapshot(SNAPSHOT_NAME, payload)
    if hits:
        # G6：写入前清理当日，防 cron 重跑重复写入
        db_delete("pattern_hits", {"date": target.isoformat()})
        db_append("pattern_hits", [
            {"date": target.isoformat(), **{k: h.get(k) for k in (
                "code", "name", "price", "platform_high", "break_pct",
                "platform_break", "gap_hold", "dual_hit", "gap_pct", "gap_type",
                "gap_low", "volume_ratio", "pattern_score")}}
            for h in hits
        ])
    return payload
