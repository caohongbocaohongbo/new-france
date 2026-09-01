"""11 分价成交编排服务。"""
import logging
from datetime import date, datetime

from backend.plugins.common import BEIJING_TZ, db_append, db_delete, db_query, json_safe, read_snapshot, write_snapshot
from backend.services.trading_calendar import is_trading_day

from .indicators import build_price_distribution, main_cost_band, poc_price, profit_ratio, vwap

logger = logging.getLogger(__name__)
SNAPSHOT_NAME = "volume_profile"


def _to_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return datetime.now(BEIJING_TZ).date()


def build_profile(code: str, name: str, bars: list, current_price) -> dict:
    """单票分价画像。"""
    distribution = build_price_distribution(bars)
    return {
        "code": str(code).zfill(6), "name": name,
        "vwap": vwap(bars),
        "poc_price": poc_price(distribution),
        "profit_ratio": profit_ratio(distribution, current_price),
        "main_cost_band": main_cost_band(distribution),
        "distribution": distribution,
        "approx": True,  # 分钟K近似，非真实逐笔分价
    }


def read_latest() -> dict:
    return read_snapshot(SNAPSHOT_NAME) or {"status": "empty", "items": []}


def read_code_profile(code: str, date_str: str = None) -> list:
    sql = "SELECT * FROM price_distribution WHERE code = :c"
    params = {"c": str(code).zfill(6)}
    if date_str:
        sql += " AND date = :d"; params["d"] = date_str
    sql += " ORDER BY price_level"
    df = db_query(sql, params)
    return json_safe(df.to_dict("records")) if not df.empty else []


def run_volume_profile_once(target_date=None, force: bool = False, bars_fetcher=None, codes=None) -> dict:
    """盘后重算分价。bars_fetcher(code) -> list[{open,high,low,close,vol,amount}]。"""
    now = datetime.now(BEIJING_TZ)
    target = _to_date(target_date) if target_date is not None else now.date()
    if not force and not is_trading_day(target):
        payload = {"status": "skipped", "reason": "非交易日", "date": target.isoformat(), "items": []}
        write_snapshot(SNAPSHOT_NAME, payload)
        return payload
    codes = codes or _watchlist_codes()
    if not codes:
        payload = {"status": "no_data", "reason": "无股票代码", "date": target.isoformat(), "items": []}
        write_snapshot(SNAPSHOT_NAME, payload)
        return payload
    items = []
    for code in codes:
        try:
            bars = bars_fetcher(code) if bars_fetcher else []
            if not bars:
                continue
            profile = build_profile(code, "", bars, None)
            items.append(profile)
            db_append("price_distribution", [
                {"date": target.isoformat(), "code": profile["code"], "price_level": r["price_level"],
                 "volume": r["volume"], "cumulative_ratio": r["cumulative_ratio"]}
                for r in profile["distribution"]
            ])
        except Exception as exc:  # noqa: BLE001
            logger.warning("分价计算失败 %s: %s", code, exc)
    payload = {"status": "completed" if items else "no_data", "date": target.isoformat(), "now": now.isoformat(), "count": len(items), "items": items}
    write_snapshot(SNAPSHOT_NAME, payload)
    return payload


def _watchlist_codes() -> list:
    try:
        from backend.services.watchlist_store import parse_watchlist, FRANCE_FILE
        return [e["code"] for e in parse_watchlist(FRANCE_FILE) if e.get("code")][:50]
    except Exception:  # noqa: BLE001
        return []
