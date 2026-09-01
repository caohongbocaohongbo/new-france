"""04 尾盘抢筹编排服务。"""
import logging
from datetime import date, datetime

from backend.api.router_system import trading_session_status
from backend.plugins.common import (
    BEIJING_TZ, db_append, db_query, float_or, json_safe, read_snapshot, write_snapshot,
)
from backend.plugins.principal_capital.service import _base_filter
from backend.plugins.principal_capital.sources.multi_source import fetch_market_fund_flow_resilient

from .indicators import is_tail_candidate, tail_acceleration, tail_fund_strength, tail_raid_score

logger = logging.getLogger(__name__)
SNAPSHOT_NAME = "tail_raid"


def _to_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return datetime.now(BEIJING_TZ).date()


def build_tail_rows(df, acceleration_map: dict = None) -> list:
    """过滤 + 打分，产出尾盘抢筹榜。"""
    acceleration_map = acceleration_map or {}
    rows = []
    for _, row in df.iterrows():
        code = str(row.get("code", "")).zfill(6)
        change_pct = float_or(row.get("change_pct"), 0) or 0
        if not is_tail_candidate(change_pct):
            continue
        volume_ratio = float_or(row.get("volume_ratio"), 1) or 1
        main_inflow_ratio = float_or(row.get("main_inflow_ratio"), 0) or 0
        total_amount = float_or(row.get("total_amount"), 0) or 0
        main_net = float_or(row.get("main_net_inflow"), 0) or 0
        acc = acceleration_map.get(code)
        score = tail_raid_score(change_pct, volume_ratio, main_inflow_ratio, acc)
        rows.append({
            "code": code, "name": str(row.get("name", "")),
            "price": float_or(row.get("price")),
            "change_pct": round(change_pct, 2),
            "volume_ratio": round(volume_ratio, 2),
            "turnover": float_or(row.get("turnover")),
            "main_inflow": main_net,
            "main_inflow_ratio": main_inflow_ratio,
            "total_amount": total_amount,
            "tail_acceleration": acc,
            "tail_fund_strength": tail_fund_strength(main_net, total_amount),
            "score": score,
        })
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[:50]


def _load_acceleration(target_date, late_hhmm="14:50", early_hhmm="14:20") -> dict:
    """从 intraday_quote_snapshot 读 14:20 与 14:50 涨幅差。"""
    df = db_query(
        "SELECT code, hhmm, change_pct FROM intraday_quote_snapshot WHERE date = :d AND hhmm IN (:a, :b)",
        {"d": target_date, "a": early_hhmm, "b": late_hhmm},
    )
    if df.empty:
        return {}
    result = {}
    for code, group in df.groupby("code"):
        by_time = {row.hhmm: row.change_pct for row in group.itertuples()}
        if early_hhmm in by_time and late_hhmm in by_time:
            result[str(code).zfill(6)] = tail_acceleration(by_time[early_hhmm], by_time[late_hhmm])
    return result


def run_tail_raid_once(now: datetime = None, force: bool = False) -> dict:
    """执行一轮尾盘抢筹扫描。"""
    now = now or datetime.now(BEIJING_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=BEIJING_TZ)
    session = trading_session_status(now)
    if not force and not session.get("is_trading_hours"):
        payload = {"status": "skipped", "reason": session.get("market_status_text", "非交易时段"), "now": now.isoformat(), "items": []}
        write_snapshot(SNAPSHOT_NAME, payload)
        return payload
    df, source_status = fetch_market_fund_flow_resilient()
    if df is None or df.empty:
        payload = {"status": "no_data", "reason": "资金流为空", "now": now.isoformat(), "items": []}
        write_snapshot(SNAPSHOT_NAME, payload)
        return payload
    filtered = _base_filter(df, exclude_star=True, min_amount=1e7)
    acc_map = _load_acceleration(now.date().isoformat())
    rows = build_tail_rows(filtered, acc_map)
    payload = {
        "status": "completed", "now": now.isoformat(),
        "active_source": (source_status or {}).get("active_source"),
        "degraded": bool((source_status or {}).get("is_stale")),
        "count": len(rows), "items": rows,
    }
    write_snapshot(SNAPSHOT_NAME, payload)
    # 落 5min 快照（最佳努力）
    db_append("intraday_quote_snapshot", [
        {"date": now.date().isoformat(), "hhmm": now.strftime("%H:%M"),
         "code": r["code"], "name": r["name"], "change_pct": r["change_pct"],
         "amount": r["total_amount"], "volume_ratio": r["volume_ratio"],
         "turnover": r["turnover"], "main_inflow": r["main_inflow"],
         "main_inflow_ratio": r["main_inflow_ratio"]}
        for r in rows
    ])
    return payload


def read_latest() -> dict:
    return read_snapshot(SNAPSHOT_NAME) or {"status": "empty", "items": []}


def read_stock_timeline(code: str, date_str: str = None) -> list:
    sql = "SELECT * FROM intraday_quote_snapshot WHERE code = :c"
    params = {"c": str(code).zfill(6)}
    if date_str:
        sql += " AND date = :d"; params["d"] = date_str
    sql += " ORDER BY date, hhmm"
    df = db_query(sql, params)
    return json_safe(df.to_dict("records")) if not df.empty else []
