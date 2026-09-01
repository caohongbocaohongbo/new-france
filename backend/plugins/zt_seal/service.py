"""10 涨停封单编排服务（云端源字段路）。"""
import logging
from datetime import date, datetime

from backend.plugins.common import (
    BEIJING_TZ, db_append, db_query, float_or, json_safe, read_snapshot, write_snapshot,
)
from backend.services.trading_calendar import is_trading_day

from .indicators import seal_amount, seal_ratio, seal_vol

logger = logging.getLogger(__name__)
SNAPSHOT_NAME = "zt_seal"


def _to_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return datetime.now(BEIJING_TZ).date()


def build_seal_rows(zt_pool) -> list:
    """由涨停池（含封板资金字段）构建封单快照行。"""
    if zt_pool is None:
        return []
    if hasattr(zt_pool, "to_dict"):
        raw = zt_pool.to_dict("records")
    else:
        raw = list(zt_pool)
    rows = []
    for r in raw or []:
        code = str(r.get("代码") or r.get("code") or "").zfill(6)
        if not code or code == "000000":
            continue
        price = float_or(r.get("最新价") or r.get("price"), 0) or 0
        fund = float_or(r.get("封板资金") or r.get("fund"))
        float_mcap = float_or(r.get("流通市值") or r.get("float_mcap"))
        rows.append({
            "code": code,
            "name": str(r.get("名称") or r.get("name") or ""),
            "price": price,
            "seal_amount": seal_amount(fund, price),
            "seal_vol": seal_vol(fund, price),
            "seal_ratio": seal_ratio(fund, float_mcap),
            "break_count": int(float_or(r.get("炸板次数") or r.get("break_count"), 0) or 0),
            "first_seal_time": int(float_or(r.get("封板时间") or r.get("first_seal_time"), 0) or 0),
            "last_seal_time": int(float_or(r.get("最后封板时间") or r.get("last_seal_time"), 0) or 0),
            "is_sealed": 1,
        })
    rows.sort(key=lambda r: r["seal_amount"] if r["seal_amount"] is not None else -1, reverse=True)
    return rows


def read_latest() -> dict:
    return read_snapshot(SNAPSHOT_NAME) or {"status": "empty", "items": []}


def read_code_history(code: str, date_str: str = None) -> list:
    sql = "SELECT * FROM zt_seal_snapshot WHERE code = :c"
    params = {"c": str(code).zfill(6)}
    if date_str:
        sql += " AND date = :d"; params["d"] = date_str
    sql += " ORDER BY date, hhmm"
    df = db_query(sql, params)
    return json_safe(df.to_dict("records")) if not df.empty else []


def run_zt_seal_once(target_date=None, force: bool = False) -> dict:
    """盘后/盘中执行一轮封单快照。"""
    now = datetime.now(BEIJING_TZ)
    target = _to_date(target_date) if target_date is not None else now.date()
    if not force and not is_trading_day(target):
        payload = {"status": "skipped", "reason": "非交易日", "date": target.isoformat(), "items": []}
        write_snapshot(SNAPSHOT_NAME, payload)
        return payload
    try:
        from backend.agents.layer1_data_collector.sources.eastmoney_zt import fetch_zt_pool
        zt_pool = fetch_zt_pool(target)
    except Exception as exc:  # noqa: BLE001
        logger.warning("涨停池拉取失败: %s", exc)
        zt_pool = None
    rows = build_seal_rows(zt_pool)
    if not rows:
        payload = {"status": "no_data", "reason": "涨停池为空", "date": target.isoformat(), "items": []}
        write_snapshot(SNAPSHOT_NAME, payload)
        return payload
    payload = {"status": "completed", "date": target.isoformat(), "now": now.isoformat(), "count": len(rows), "items": rows}
    write_snapshot(SNAPSHOT_NAME, payload)
    db_append("zt_seal_snapshot", [
        {"date": target.isoformat(), "hhmm": now.strftime("%H:%M"),
         **{k: r.get(k) for k in ("code", "seal_amount", "seal_vol", "break_count", "first_seal_time", "last_seal_time", "is_sealed")}}
        for r in rows
    ])
    return payload
