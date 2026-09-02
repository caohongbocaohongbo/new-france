"""15 四维共振编排服务（修复版：tier_flow 顺序约束 + D1 持续性 + D3 两路匹配）。
17 升级：盘中实时化（D2/D4 intraday）+ 新信号推送 + upgrade_hint。
"""
import asyncio
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from backend.api.router_system import trading_session_status
from backend.plugins.common import (
    BEIJING_TZ, db_append, db_delete, db_query, float_or, json_safe, market_filter,
    read_snapshot, write_snapshot,
)
from backend.plugins.principal_capital.service import should_notify
from backend.plugins.principal_capital.sources.multi_source import fetch_market_fund_flow_resilient
from backend.plugins.principal_capital.tier_flow import classify_state
from backend.services.trading_calendar import is_trading_day

from .config import CONFIG, DATA_DIR, SNAPSHOT_NAME
from .indicators import (
    _intraday_progress, compute_resonance, d1_score, d2_score, d2_score_intraday,
    d3_score, d4_score, d4_score_intraday,
)
from .notifier import notify_new_signals

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


# ==== 17 盘中推送冷却去重 ====

def _notified_file(today: date) -> Path:
    return DATA_DIR / f"resonance_notified_{today.isoformat()}.json"


def load_notified_map(today: date) -> dict:
    path = _notified_file(today)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("notified") or {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_notified_map(today: date, notified_map: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _notified_file(today).write_text(
        json.dumps({"date": today.isoformat(), "notified": notified_map}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _update_notified_map(notified_map: dict, hits: list, now: datetime) -> dict:
    for item in hits or []:
        code = str(item.get("code") or "").zfill(6)
        notified_map.setdefault(code, []).append(now.isoformat())
    return notified_map


def compute_d1(row: dict) -> dict:
    """由资金流行计算 D1 原始量：smart_ratio + state。"""
    total = float_or(row.get("total_amount"), 0) or 0
    super_net = float_or(row.get("super_net"), 0) or 0
    big_net = float_or(row.get("big_net"), 0) or 0
    change_pct = float_or(row.get("change_pct"), 0) or 0
    smart_net = super_net + big_net
    # d1_score 期望小数（clip [-0.05, 0.05]），此处存 fraction 而非百分比
    smart_ratio = round(smart_net / total, 4) if total > 0 else 0.0
    return {"smart_ratio": smart_ratio, "state": classify_state(smart_net, change_pct)}


def coarse_filter(df: pd.DataFrame, cfg: dict = None) -> list:
    """D1 粗筛：市场过滤(默认主板) + state ∈ {吸筹,拉升,出货}。返回 [(row, d1)]。"""
    cfg = cfg or CONFIG
    if df is None or df.empty:
        return []
    filtered = market_filter(df, show_gem=cfg.get("show_gem", False),
                             show_star=cfg.get("show_star", False))
    out = []
    for _, row in filtered.iterrows():
        d1 = compute_d1(row)
        if d1["state"] in {"吸筹", "拉升", "出货"}:
            out.append((row, d1))
    return out


def load_continuity_map(date_str: str) -> dict:
    """从 tier_flow_snapshot 聚合当日各 code 的轮次数（D1 持续性代理）。"""
    df = db_query(
        "SELECT code, COUNT(*) AS rounds FROM tier_flow_snapshot WHERE date = :d GROUP BY code",
        {"d": date_str},
    )
    if df.empty:
        return {}
    return {str(r.code).zfill(6): int(r.rounds or 0) for r in df.itertuples()}


def ensure_tier_flow(target_date: date, runner=None) -> int:
    """修复1：resonance 依赖 tier_flow_snapshot，当日为空则先补跑一次。返回条数。"""
    df = db_query("SELECT COUNT(*) AS c FROM tier_flow_snapshot WHERE date = :d", {"d": target_date.isoformat()})
    count = int(df.iloc[0]["c"]) if not df.empty else 0
    if count == 0:
        from backend.plugins.principal_capital.tier_flow import run_tier_flow_once
        (runner or run_tier_flow_once)(force=True)
        df = db_query("SELECT COUNT(*) AS c FROM tier_flow_snapshot WHERE date = :d", {"d": target_date.isoformat()})
        count = int(df.iloc[0]["c"]) if not df.empty else 0
    return count


def extract_ohlcv(hist):
    if hist is None or getattr(hist, "empty", True):
        return None, None, None
    def col(*names):
        for n in names:
            if n in hist.columns:
                return hist[n].tolist()
        return None
    return col("收盘", "close"), col("最高", "high"), col("成交量", "vol", "volume")


def evaluate_resonance(code: str, name: str, price, d1: dict, continuity_rounds: int,
                       closes, highs, vols, board_heat, db_session, cfg: dict = None,
                       is_intraday: bool = False, realtime_price=None,
                       intraday_vol=None, prev_avg_vol=None, time_progress=None) -> Optional[dict]:
    """单票四维融合 + 信号灯（盘中用实时价/日内量重算 D2/D4）。"""
    cfg = cfg or CONFIG
    d1_v = d1_score(d1["smart_ratio"], d1["state"], continuity_rounds)
    d2_realtime = 0
    if is_intraday:
        d2 = d2_score_intraday(closes, highs, realtime_price)
        if d2 is not None:
            d2_realtime = 1
        else:
            d2 = d2_score(closes, highs)  # 盘前/无实时价 → 降级日线 D2
        d4_result = d4_score_intraday(vols, intraday_vol, prev_avg_vol, time_progress)
    else:
        d2 = d2_score(closes, highs)
        d4_result = d4_score(vols)
    d3_result = d3_score(code, board_heat, db_session)
    r = compute_resonance(d1_v, d1["state"], d2, d3_result, d4_result,
                          cfg["red_threshold"], cfg["green_threshold"])
    return {
        "code": str(code).zfill(6), "name": name, "price": float_or(price),
        "resonance_score": r["resonance_score"], "signal": r["signal"],
        "d1_score": d1_v, "d1_state": d1["state"],
        "d1_continuity_rounds": int(continuity_rounds),
        "d2_score": d2, "d3_score": d3_result[0], "d4_score": d4_result[0],
        "d4_vol_ratio": r["vol_ratio"],
        "d3_degraded": r["d3_degraded"], "d3_source": r["d3_source"],
        "d2_insufficient": r["d2_insufficient"],
        "d2_realtime": d2_realtime,
    }


def _prev_avg_vol(vols: list, window: int = 5):
    """过去 window 日日均量（用于 D4 日内量比分母）。"""
    vals = [float_or(v) for v in (vols or []) if float_or(v) is not None and float_or(v) > 0]
    if len(vals) < window:
        return None
    return sum(vals[-window:]) / window


async def _scan(coarse: list, continuity_map: dict, board_heat, db_session,
                kline_fetcher: Callable, workers: int, cfg: dict = None,
                is_intraday: bool = False, time_progress=None) -> list:
    """并发拉 K 线并四维融合，Semaphore 限流。"""
    cfg = cfg or CONFIG
    semaphore = asyncio.Semaphore(max(1, int(workers)))

    async def scan(row, d1):
        async with semaphore:
            code = str(row.get("code", "")).zfill(6)
            try:
                hist = await asyncio.to_thread(kline_fetcher, code, 60)
                closes, highs, vols = extract_ohlcv(hist)
            except Exception as exc:  # noqa: BLE001
                logger.debug("K线拉取失败 %s: %s", code, exc)
                closes = highs = vols = None
            realtime_price = float_or(row.get("price")) if is_intraday else None
            intraday_vol = float_or(row.get("vol")) if is_intraday else None
            prev_avg_vol = _prev_avg_vol(vols) if is_intraday else None
            return evaluate_resonance(code, str(row.get("name", "")), row.get("price"),
                                      d1, continuity_map.get(code, 0),
                                      closes, highs, vols, board_heat, db_session, cfg,
                                      is_intraday=is_intraday, realtime_price=realtime_price,
                                      intraday_vol=intraday_vol, prev_avg_vol=prev_avg_vol,
                                      time_progress=time_progress)

    results = await asyncio.gather(*(scan(row, d1) for row, d1 in coarse), return_exceptions=True)
    hits = [r for r in results if isinstance(r, dict)]
    hits.sort(key=lambda r: r["resonance_score"], reverse=True)
    return hits


def _suggested_adjustment(red_count: int):
    """修复4：阈值只读建议，不自动收紧。"""
    if red_count > 100:
        return "+3"
    if red_count < 5:
        return "-3"
    return None


def read_latest() -> dict:
    return read_snapshot(SNAPSHOT_NAME) or {"status": "empty", "items": []}


def read_code_history(code: str, date_str: str = None) -> list:
    sql = "SELECT * FROM resonance_snapshot WHERE code = :c"
    params = {"c": str(code).zfill(6)}
    if date_str:
        sql += " AND date = :d"; params["d"] = date_str
    sql += " ORDER BY date, hhmm"
    df = db_query(sql, params)
    return json_safe(df.to_dict("records")) if not df.empty else []


def read_code_kline(code: str, days: int = 60) -> list:
    """17 路径B：个股日线 OHLC（供 K 线副图），失败返回空列表。"""
    try:
        from backend.agents.layer1_data_collector.sources.historical_kline import fetch_historical

        hist = fetch_historical(str(code).zfill(6), int(days))
    except Exception:  # noqa: BLE001
        return []
    if hist is None or getattr(hist, "empty", True):
        return []

    def col(*names):
        for n in names:
            if n in hist.columns:
                return hist[n].tolist()
        return None

    dates = col("日期", "date")
    opens = col("开盘", "open")
    closes = col("收盘", "close")
    highs = col("最高", "high")
    lows = col("最低", "low")
    vols = col("成交量", "vol", "volume")
    records = []
    for i in range(len(hist)):
        records.append({
            "date": str(dates[i])[:10] if dates else None,
            "open": float_or(opens[i]) if opens else None,
            "close": float_or(closes[i]) if closes else None,
            "high": float_or(highs[i]) if highs else None,
            "low": float_or(lows[i]) if lows else None,
            "vol": float_or(vols[i]) if vols else None,
        })
    return json_safe(records)


def _distinct_days_count() -> int:
    """resonance_snapshot 已积累的交易日数。"""
    df = db_query("SELECT COUNT(DISTINCT date) AS c FROM resonance_snapshot")
    return int(df.iloc[0]["c"]) if not df.empty else 0


async def run_resonance_once(target_date=None, force: bool = False,
                             fund_flow_fetcher=None, kline_fetcher=None,
                             board_heat_loader=None, tier_flow_runner=None,
                             continuity_loader=None, db_session=None,
                             max_kline_workers: int = None, notifier=None) -> dict:
    """盘后/盘中执行一轮四维共振扫描（盘中用实时价/日内量 + 新信号推送）。"""
    now = datetime.now(BEIJING_TZ)
    target = _to_date(target_date) if target_date is not None else now.date()
    if not force and not is_trading_day(target):
        payload = {"status": "skipped", "reason": "非交易日", "date": target.isoformat(), "items": []}
        write_snapshot(SNAPSHOT_NAME, payload)
        return payload
    # 修复1：tier_flow 必须先于 resonance 执行
    ensure_tier_flow(target, tier_flow_runner)
    df, source_status = (fund_flow_fetcher or fetch_market_fund_flow_resilient)()
    if df is None or df.empty:
        payload = {"status": "no_data", "reason": "资金流为空", "date": target.isoformat(), "items": []}
        write_snapshot(SNAPSHOT_NAME, payload)
        return payload
    coarse = coarse_filter(df)
    if not coarse:
        payload = {"status": "no_data", "reason": "D1 粗筛后无候选", "date": target.isoformat(), "items": []}
        write_snapshot(SNAPSHOT_NAME, payload)
        return payload
    continuity_map = (continuity_loader or load_continuity_map)(target.isoformat())
    board_heat = (board_heat_loader or read_snapshot)("board_heat")
    workers = int(max_kline_workers or CONFIG["kline_workers"])
    if kline_fetcher is None:
        from backend.agents.layer1_data_collector.sources.historical_kline import fetch_historical as kline_fetcher

    # 17：盘中实时化判定 + 上一轮快照（对比新增信号）
    session = trading_session_status(now)
    is_intraday = bool(session.get("is_trading_hours"))
    time_progress = _intraday_progress(now) if is_intraday else None
    prev_snapshot = read_snapshot(SNAPSHOT_NAME) or {}
    prev_red = {str(it.get("code")) for it in prev_snapshot.get("items", []) if it.get("signal") == "RED"}
    prev_green = {str(it.get("code")) for it in prev_snapshot.get("items", []) if it.get("signal") == "GREEN"}

    hits = await _scan(coarse, continuity_map, board_heat, db_session, kline_fetcher, workers,
                       is_intraday=is_intraday, time_progress=time_progress)
    signal_counts = {"RED": 0, "YELLOW": 0, "GREEN": 0}
    for h in hits:
        signal_counts[h["signal"]] = signal_counts.get(h["signal"], 0) + 1

    # 17：盘中推送新增 RED/GREEN，30min 冷却去重
    email_sent = False
    email_error = None
    notified_codes = set()
    if is_intraday:
        new_red = [h for h in hits if h["signal"] == "RED" and h["code"] not in prev_red]
        new_green = [h for h in hits if h["signal"] == "GREEN" and h["code"] not in prev_green]
        if new_red or new_green:
            cooldown = int(CONFIG["notify_cooldown_minutes"])
            notified_map = load_notified_map(target)
            new_red = [h for h in new_red if should_notify(h["code"], "resonance", now, notified_map, cooldown)]
            new_green = [h for h in new_green if should_notify(h["code"], "resonance", now, notified_map, cooldown)]
            if new_red or new_green:
                email_sent, email_error = (notifier or notify_new_signals)(new_red, new_green, now)
                if email_sent:
                    notified_codes = {h["code"] for h in new_red + new_green}
                    save_notified_map(target, _update_notified_map(notified_map, new_red + new_green, now))

    payload = {
        "status": "completed", "date": target.isoformat(), "now": now.isoformat(),
        "active_source": (source_status or {}).get("active_source"),
        "count": len(hits), "signal_counts": signal_counts,
        "suggested_threshold_adjustment": _suggested_adjustment(signal_counts.get("RED", 0)),
        "is_intraday": is_intraday,
        "email_sent": bool(email_sent), "email_error": email_error,
        "items": hits[:40],
        "disclaimer": "信号仅为辅助参考，不构成投资建议",
    }
    # 17 §2.4：积累满 20 个交易日 → 注入路径 B 升级提示
    days_count = _distinct_days_count()
    if days_count >= int(CONFIG["upgrade_hint_days"]):
        payload["upgrade_hint"] = (
            "resonance_snapshot 已积累 %d 个交易日，"
            "路径 B（ECharts K线副图）方案可以实施，请启动方案 17 路径B施工。" % days_count
        )
        payload["trading_days_accumulated"] = int(days_count)
    write_snapshot(SNAPSHOT_NAME, payload)
    if hits:
        db_delete("resonance_snapshot", {"date": target.isoformat()})
        db_append("resonance_snapshot", [
            {"date": target.isoformat(), "hhmm": now.strftime("%H:%M"),
             "code": h.get("code"), "name": h.get("name"),
             "resonance_score": h.get("resonance_score"), "signal": h.get("signal"),
             "d1_score": h.get("d1_score"), "d1_state": h.get("d1_state"),
             "d2_score": h.get("d2_score"), "d3_score": h.get("d3_score"),
             "d4_score": h.get("d4_score"),
             "active_source": (source_status or {}).get("active_source"),
             "degraded": 1 if h.get("d3_degraded") else 0,
             "d2_realtime": int(h.get("d2_realtime") or 0),
             "notified": 1 if h.get("code") in notified_codes else 0}
            for h in hits
        ])
    return payload
