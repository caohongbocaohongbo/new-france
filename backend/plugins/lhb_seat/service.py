"""龙虎榜编排服务（离线可测，fetch 可注入）。"""
import logging
from datetime import date, datetime

from backend.plugins.common import (
    BEIJING_TZ, db_append, db_delete, db_query, float_or, json_safe, read_snapshot, write_snapshot,
)
from backend.services.trading_calendar import is_trading_day

from .config import CONFIG, SNAPSHOT_NAME
from .indicators import build_seat_profile, classify_seat_type, known_seat_label, match_confidence, normalize_seat_name

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


def fetch_lhb_daily_akshare(target_date: date) -> list:
    """akShare 龙虎榜日榜单 -> list[dict]。失败返回 []。"""
    try:
        import akshare as ak

        d = target_date.strftime("%Y%m%d")
        df = ak.stock_lhb_detail_em(start_date=d, end_date=d)
    except Exception as exc:  # noqa: BLE001
        logger.warning("龙虎榜拉取失败: %s", exc)
        return []
    if df is None or df.empty:
        return []
    rows = []
    for _, r in df.iterrows():
        code = str(r.get("代码", "")).zfill(6)
        if not code or code == "000000":
            continue
        rows.append({
            "date": target_date.isoformat(),
            "code": code,
            "name": str(r.get("名称", "")),
            "reason": str(r.get("上榜原因") or r.get("解读") or ""),
            "net_buy": float_or(r.get("龙虎榜净买额")),
            "buy_amount": float_or(r.get("龙虎榜买入额")),
            "sell_amount": float_or(r.get("龙虎榜卖出额")),
            "change_pct": float_or(r.get("涨跌幅")),
            "close_price": float_or(r.get("收盘价")),
            "float_mcap": float_or(r.get("流通市值")),
            "turnover": float_or(r.get("换手率")),
        })
    return rows


def fetch_seats_akshare(code: str, target_date: date) -> list:
    """akShare 单股买入/卖出席位 -> list[dict]。失败返回 []。"""
    seats = []
    d = target_date.strftime("%Y%m%d")
    try:
        import akshare as ak
        for flag, side in (("买入", "buy"), ("卖出", "sell")):
            df = ak.stock_lhb_stock_detail_em(symbol=code, date=d, flag=flag)
            if df is None or df.empty:
                continue
            for _, r in df.iterrows():
                name = str(r.get("营业部名称") or r.get("席位名称") or "")
                if not name:
                    continue
                seats.append({
                    "date": target_date.isoformat(), "code": code,
                    "seat_name": name, "side": side,
                    "amount": float_or(r.get("买入额") if side == "buy" else r.get("卖出额")),
                })
    except Exception as exc:  # noqa: BLE001
        logger.debug("席位明细拉取失败 %s: %s", code, exc)
    return seats


def normalize_seats(seats: list) -> list:
    """席位归一化 + 类型标注 + 置信度。"""
    out = []
    for s in seats or []:
        raw = s.get("seat_name", "")
        norm = normalize_seat_name(raw)
        label = known_seat_label(raw)
        out.append({
            **s,
            "normalized_name": norm,
            "seat_type": classify_seat_type(raw),
            "known_label": label,
            "match_confidence": match_confidence(raw),
        })
    return out


def aggregate_seat_stats(seats: list) -> list:
    """把归一化席位聚合为频率画像（胜率需历史收益样本，初始为 None）。"""
    agg = {}
    for s in seats or []:
        key = s.get("normalized_name")
        if not key:
            continue
        entry = agg.setdefault(key, {"seat_name": key, "appearances": 0, "total_amount": 0.0, "sides": [], "seat_type": s.get("seat_type"), "known_label": s.get("known_label")})
        entry["appearances"] += 1
        entry["total_amount"] = (entry["total_amount"] or 0.0) + (float_or(s.get("amount"), 0) or 0)
        entry["sides"].append(s.get("side"))
    result = []
    for key, entry in agg.items():
        profile = build_seat_profile([])  # 无收益样本 → 胜率 None
        result.append({
            "normalized_name": key,
            "seat_type": entry["seat_type"],
            "known_label": entry["known_label"],
            "appearances": entry["appearances"],
            "total_amount": round(entry["total_amount"], 2),
            "win_rate_t1": profile.get("win_rate_t1"),
            "win_rate_t3": profile.get("win_rate_t3"),
            "win_rate_t5": profile.get("win_rate_t5"),
        })
    result.sort(key=lambda x: x["appearances"], reverse=True)
    return result


def read_latest() -> dict:
    return read_snapshot(SNAPSHOT_NAME) or {"status": "empty", "items": []}


def read_seat_profile(name: str) -> dict:
    df = db_query("SELECT * FROM seat_stats WHERE seat_name = :n", {"n": name})
    if df.empty:
        return {"normalized_name": name, "sample_count": 0, "message": "暂无该席位画像"}
    return json_safe(df.iloc[0].to_dict())


def read_stock_lhb(code: str) -> list:
    df = db_query(
        "SELECT * FROM lhb_daily WHERE code = :c ORDER BY date DESC",
        {"c": str(code).zfill(6)},
    )
    return json_safe(df.to_dict("records")) if not df.empty else []


def run_lhb_once(target_date=None, force: bool = False, fetcher=None, seat_fetcher=None) -> dict:
    """盘后执行一轮龙虎榜采集与画像。"""
    now = datetime.now(BEIJING_TZ)
    target = _to_date(target_date) if target_date is not None else now.date()
    if not force and not is_trading_day(target):
        payload = {"status": "skipped", "reason": "非交易日", "date": target.isoformat(), "items": []}
        write_snapshot(SNAPSHOT_NAME, payload)
        return payload
    rows = (fetcher or fetch_lhb_daily_akshare)(target)
    if not rows:
        payload = {"status": "no_data", "reason": "龙虎榜为空", "date": target.isoformat(), "items": []}
        write_snapshot(SNAPSHOT_NAME, payload)
        return payload
    # 席位明细：按净买额取前 N 只做最佳努力采集
    top = sorted(rows, key=lambda r: abs(r.get("net_buy") or 0), reverse=True)[:int(CONFIG["max_seat_detail_stocks"])]
    seats = []
    for item in top:
        seats.extend((seat_fetcher or fetch_seats_akshare)(item["code"], target))
    seats = normalize_seats(seats)
    seat_stats = aggregate_seat_stats(seats)
    payload = {
        "status": "completed", "date": target.isoformat(), "now": now.isoformat(),
        "count": len(rows), "seat_count": len(seats),
        "items": rows, "seat_stats": seat_stats,
    }
    write_snapshot(SNAPSHOT_NAME, payload)
    # 落 SQLite（最佳努力）
    db_delete("lhb_daily", {"date": target.isoformat()})
    db_append("lhb_daily", rows)
    db_append("lhb_seats", seats)
    db_append("seat_stats", [
        {"seat_name": s["normalized_name"], "updated_at": now.isoformat(),
         "win_rate_t1": s.get("win_rate_t1"), "win_rate_t3": s.get("win_rate_t3"),
         "win_rate_t5": s.get("win_rate_t5"),
         "prefer_board": None, "prefer_theme": None}
        for s in seat_stats
    ])
    return payload
