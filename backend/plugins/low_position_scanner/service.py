"""16 低位涨停选股器编排服务（修复版：AND-OR 低位 + 四级涨停降级）。"""
import asyncio
import logging
import os
import time
from datetime import date, datetime, timedelta
from typing import Callable, Optional

import pandas as pd

from backend.plugins.common import (
    BEIJING_TZ, db_append, db_delete, db_query, float_or, json_safe, market_filter,
    read_snapshot, write_snapshot,
)
from backend.plugins.principal_capital.sources.multi_source import fetch_market_fund_flow_resilient
from backend.services.trading_calendar import is_trading_day

from .config import CONFIG, SNAPSHOT_NAME
from .indicators import accel_down_factor, compute_low_score, is_low_position

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
    """粗筛：市场过滤(默认主板) + 成交额下限 + 流通市值区间。"""
    cfg = cfg or CONFIG
    if df is None or df.empty:
        return pd.DataFrame()
    filtered = market_filter(df, show_gem=cfg.get("show_gem", False),
                             show_star=cfg.get("show_star", False),
                             min_amount=float(cfg["min_amount"]))
    if "float_mcap" in filtered.columns:
        mcap = filtered["float_mcap"]
        filtered = filtered[mcap.notna() & (mcap >= float(cfg["mcap_min"])) & (mcap <= float(cfg["mcap_max"]))]
    return filtered.reset_index(drop=True)


# ==== 涨停历史：四级降级 ====

def build_zt_history_map_from_db() -> dict:
    """一级：limit_up_daily 表（01 方案落库，近 250 交易日口径）。"""
    df = db_query("SELECT code, MAX(date) AS last_zt_date, COUNT(*) AS zt_count FROM limit_up_daily GROUP BY code")
    if df.empty:
        return {}
    result = {}
    for r in df.itertuples():
        result[str(r.code).zfill(6)] = {"last_zt_date": str(r.last_zt_date), "zt_count": int(r.zt_count or 0)}
    return result


def build_zt_history_map_from_tushare() -> dict:
    """二级：Tushare limit_list_d（有 TUSHARE_TOKEN 时），历史回补。"""
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        return {}
    try:
        import tushare as ts

        pro = ts.pro_api(token)
        end = datetime.now(BEIJING_TZ).strftime("%Y%m%d")
        start = (datetime.now(BEIJING_TZ) - timedelta(days=400)).strftime("%Y%m%d")
        df = pro.limit_list_d(start_date=start, end_date=end, limit_type="U")
    except Exception as exc:  # noqa: BLE001
        logger.info("Tushare 涨停历史拉取失败(降级): %s", exc)
        return {}
    if df is None or df.empty:
        return {}
    if len(df) < 100:
        # 积分不足/无权限时 Tushare 会返回空表或少量行，显式告警避免静默降级
        logger.warning("Tushare limit_list_d 返回行数异常（%d），可能积分不足，将降级到 akShare", len(df))
        return {}
    result = {}
    for _, r in df.iterrows():
        code = str(r.get("ts_code", "")).split(".")[0].zfill(6)
        d = str(r.get("trade_date", ""))[:10]
        entry = result.setdefault(code, {"last_zt_date": "", "zt_count": 0})
        entry["zt_count"] += 1
        if d > entry["last_zt_date"]:
            entry["last_zt_date"] = d
    return result


def build_zt_history_map_from_akshare(target_date: date, max_days: int = 30) -> dict:
    """三级：akShare stock_zt_pool_em 按日循环，最多拼 30 日，带限速。"""
    result = {}
    for i in range(max(1, int(max_days))):
        if i > 0:
            time.sleep(0.3)  # 限速：每轮请求前等待，首次不等待，末次成功后不再多余 sleep
        d = target_date - timedelta(days=i)
        try:
            import akshare as ak
            df = ak.stock_zt_pool_em(date=d.strftime("%Y%m%d"))
        except Exception:  # noqa: BLE001 单日失败跳过
            continue
        if df is not None and not df.empty:
            for _, r in df.iterrows():
                code = str(r.get("代码", "")).zfill(6)
                entry = result.setdefault(code, {"last_zt_date": "", "zt_count": 0})
                entry["zt_count"] += 1
                if d.isoformat() > entry["last_zt_date"]:
                    entry["last_zt_date"] = d.isoformat()
    return result


def resolve_zt_map(target_date: date, fetchers: dict = None) -> tuple:
    """四级降级：db → tushare → akshare → unavailable。返回 (map, source, available)。"""
    fetchers = fetchers or {}
    sources = [
        ("db", fetchers.get("db") or build_zt_history_map_from_db),
        ("tushare", fetchers.get("tushare") or build_zt_history_map_from_tushare),
        ("akshare", fetchers.get("akshare") or (lambda: build_zt_history_map_from_akshare(target_date))),
    ]
    for source, fn in sources:
        try:
            m = fn()
            if m:
                return m, source, True
        except Exception as exc:  # noqa: BLE001
            logger.info("涨停历史 %s 降级失败: %s", source, exc)
    return {}, None, False


def extract_closes(hist):
    if hist is None or getattr(hist, "empty", True):
        return None
    for col in ("收盘", "close"):
        if col in hist.columns:
            return [float_or(v) for v in hist[col].tolist()]
    return None


def evaluate_candidate(code: str, name: str, price, total_amount, float_mcap,
                       closes: list, zt_map: dict, zt_source, zt_available: bool,
                       cfg: dict = None) -> Optional[dict]:
    """单票精算：AND-OR 低位 + 涨停历史(可用时) + 负向动能 + 综合分。"""
    cfg = cfg or CONFIG
    if not closes or len(closes) < int(cfg["ma_period"]):
        return None
    is_low, detail = is_low_position(closes, cfg["pullback_min"], cfg["percentile_max"], cfg["ma_period"])
    if not is_low:
        return None
    zt_info = zt_map.get(str(code).zfill(6), {})
    # 涨停历史可用但无记录 → 剔除；不可用时不因数据缺失剔除
    if zt_available and not zt_info:
        return None
    penalty = accel_down_factor(closes, cfg["down_window"])
    score = compute_low_score(detail["pullback_pct"], detail["price_percentile"],
                              zt_info.get("zt_count", 0), penalty, cfg["weights"])
    return {
        "code": str(code).zfill(6), "name": name,
        "price": float_or(price),
        "pullback_pct": detail["pullback_pct"],
        "price_percentile": detail["price_percentile"],
        "below_ma20": 1 if detail["below_ma20"] else 0,
        "last_zt_date": zt_info.get("last_zt_date"),
        "zt_count_250d": zt_info.get("zt_count", 0),
        "zt_source": zt_source if zt_info else None,
        "zt_available": zt_available,
        "accel_down_penalty": penalty,
        "market_cap": float_or(float_mcap),
        "turnover": None,
        "total_amount": float_or(total_amount),
        "low_score": score,
    }


async def _scan_coarse(coarse: pd.DataFrame, zt_map: dict, zt_source, zt_available: bool,
                       kline_fetcher: Callable, workers: int, kline_days: int) -> list:
    """并发拉 K 线并精算，Semaphore 限流。"""
    semaphore = asyncio.Semaphore(max(1, int(workers)))

    async def scan(row):
        async with semaphore:
            code = str(row.get("code", "")).zfill(6)
            try:
                hist = await asyncio.to_thread(kline_fetcher, code, kline_days)
                closes = extract_closes(hist)
            except Exception as exc:  # noqa: BLE001
                logger.debug("K线拉取失败 %s: %s", code, exc)
                closes = None
            return evaluate_candidate(code, str(row.get("name", "")), row.get("price"),
                                      row.get("total_amount"), row.get("float_mcap"),
                                      closes, zt_map, zt_source, zt_available)

    results = await asyncio.gather(*(scan(row) for _, row in coarse.iterrows()), return_exceptions=True)
    hits = [r for r in results if isinstance(r, dict)]
    hits.sort(key=lambda r: r["low_score"], reverse=True)
    return hits


def read_latest() -> dict:
    return read_snapshot(SNAPSHOT_NAME) or {"status": "empty", "items": []}


def read_code_hits(code: str, date_str: str = None) -> list:
    sql = "SELECT * FROM low_position_hits WHERE code = :c"
    params = {"c": str(code).zfill(6)}
    if date_str:
        sql += " AND date = :d"; params["d"] = date_str
    sql += " ORDER BY date DESC"
    df = db_query(sql, params)
    return json_safe(df.to_dict("records")) if not df.empty else []


async def run_low_position_once(target_date=None, force: bool = False,
                                fund_flow_fetcher=None, kline_fetcher=None,
                                zt_fetchers: dict = None, max_kline_workers: int = None) -> dict:
    """盘后执行一轮低位涨停选股。"""
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
    zt_map, zt_source, zt_available = resolve_zt_map(target, zt_fetchers)
    workers = int(max_kline_workers or CONFIG["kline_workers"])
    if kline_fetcher is None:
        from backend.agents.layer1_data_collector.sources.historical_kline import fetch_historical as kline_fetcher
    hits = await _scan_coarse(coarse, zt_map, zt_source, zt_available,
                              kline_fetcher, workers, int(CONFIG["kline_days"]))
    payload = {
        "status": "completed", "date": target.isoformat(), "now": now.isoformat(),
        "active_source": (source_status or {}).get("active_source"),
        "zt_source": zt_source, "zt_available": zt_available,
        "total_count": len(hits), "items": hits,
        "disclaimer": "本结果仅为量化筛选，不构成投资建议",
    }
    write_snapshot(SNAPSHOT_NAME, payload)
    if hits:
        db_delete("low_position_hits", {"date": target.isoformat()})
        db_append("low_position_hits", [
            {"date": target.isoformat(), **{k: h.get(k) for k in ("code", "name", "price", "pullback_pct", "price_percentile", "below_ma20", "last_zt_date", "zt_count_250d", "market_cap", "turnover", "total_amount", "low_score", "zt_source")}}
            for h in hits
        ])
    return payload
