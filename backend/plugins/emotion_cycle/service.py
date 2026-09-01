"""情绪周期编排服务。"""
import logging
from datetime import date, datetime

from backend.plugins.common import (
    BEIJING_TZ, db_append, db_delete, db_query, json_safe, read_snapshot, write_snapshot,
)
from backend.services.trading_calendar import is_trading_day, prev_trading_day

from .config import CONFIG, SNAPSHOT_NAME
from .indicators import compute_emotion, parse_zt_records

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


def _load_prev_heights(date_str: str) -> dict:
    df = db_query("SELECT code, board_height FROM limit_up_daily WHERE date = :d", {"d": date_str})
    if df.empty:
        return {}
    result = {}
    for row in df.itertuples():
        result[str(row.code).zfill(6)] = int(row.board_height or 1)
    return result


def _load_prev_score():
    df = db_query("SELECT score FROM emotion_daily ORDER BY date DESC LIMIT 1")
    if df.empty or df.iloc[0]["score"] is None:
        return None
    return float(df.iloc[0]["score"])


def read_latest() -> dict:
    return read_snapshot(SNAPSHOT_NAME) or {"status": "empty", "items": []}


def read_history(days: int = 90) -> list:
    df = db_query("SELECT date, score, regime, metrics_json FROM emotion_daily ORDER BY date DESC LIMIT :n", {"n": int(days)})
    if df.empty:
        return []
    return json_safe(df.to_dict("records"))


def read_ladder(date_str: str) -> list:
    df = db_query(
        "SELECT * FROM limit_up_daily WHERE date = :d ORDER BY board_height DESC, seal_time ASC",
        {"d": date_str},
    )
    if df.empty:
        return []
    return json_safe(df.to_dict("records"))


def run_emotion_once(target_date=None, force: bool = False) -> dict:
    """盘后执行一轮情绪周期计算。"""
    now = datetime.now(BEIJING_TZ)
    target = _to_date(target_date) if target_date is not None else now.date()
    if not force and not is_trading_day(target):
        payload = {"status": "skipped", "reason": "非交易日", "date": target.isoformat(), "regime": "no_data"}
        write_snapshot(SNAPSHOT_NAME, payload)
        return payload

    try:
        from backend.agents.layer1_data_collector.sources.eastmoney_zt import fetch_zt_pool
        zt_pool = fetch_zt_pool(target)
    except Exception as exc:  # noqa: BLE001
        logger.warning("涨停池拉取失败: %s", exc)
        zt_pool = None
    records = parse_zt_records(zt_pool)
    if not records:
        payload = {"status": "no_data", "reason": "涨停池为空", "date": target.isoformat(), "regime": "no_data", "count": 0}
        write_snapshot(SNAPSHOT_NAME, payload)
        return payload

    try:
        from backend.agents.layer1_data_collector.sources.index_data import fetch_index_gain
        index_gain = fetch_index_gain()
    except Exception:  # noqa: BLE001
        index_gain = 0.0

    prev_d = prev_trading_day(target)
    prev_heights = _load_prev_heights(prev_d.isoformat()) if prev_d else {}
    prev_score = _load_prev_score()
    emotion = compute_emotion(records, prev_heights, index_gain, CONFIG["weights"], prev_score)
    payload = {
        "status": "completed", "date": target.isoformat(), "now": now.isoformat(),
        "count": len(records), **emotion,
    }
    write_snapshot(SNAPSHOT_NAME, payload)

    # 落 SQLite（最佳努力）
    db_delete("emotion_daily", {"date": target.isoformat()})
    db_append("limit_up_daily", [
        {
            "date": target.isoformat(), "code": r["code"], "name": r["name"],
            "board_height": r["height"], "seal_time": r["seal_time"],
            "break_count": r["break_count"], "first_seal_time": r["seal_time"],
            "industry": r["industry"], "concept": "", "is_trap": 0,
        }
        for r in records
    ])
    db_append("emotion_daily", [{
        "date": target.isoformat(), "metrics_json": json_safe(emotion.get("metrics") or {}),
        "score": emotion.get("score"), "regime": emotion.get("regime"),
        "created_at": now.isoformat(),
    }])
    return payload
