"""26 事件驱动雷达编排服务：扫描 + 去重 + 影响打分 + 监控列表交叉。"""
import hashlib
import json
import logging

from backend.plugins.common import (
    BEIJING_TZ, db_append, db_query, json_safe, now_beijing, read_snapshot, write_snapshot,
)

from .config import SNAPSHOT_NAME
from .scoring import classify_score
from .sources import ALL_SOURCES

logger = logging.getLogger(__name__)

MAIN_BOARD = ("600", "601", "603", "605", "000", "001", "002", "003")


def _watch_set() -> set:
    try:
        from backend.services.watchlist_store import parse_watchlist
        items = parse_watchlist()
        return {str(c).zfill(6) for c in items if str(c).zfill(6).startswith(MAIN_BOARD)}
    except Exception:  # noqa: BLE001
        return set()


def _dedup_key(code, title, source) -> str:
    raw = f"{code}:{title}:{source}".encode("utf-8")
    return hashlib.md5(raw).hexdigest()[:24]


def run_scan_once() -> dict:
    """扫描全部源，打分去重，写快照 + SQLite。"""
    events = []
    watch_set = _watch_set()
    for source, fn in ALL_SOURCES.items():
        try:
            for item in fn():
                code = str(item.get("code", "")).zfill(6)
                if not code.startswith(MAIN_BOARD):
                    continue
                scored = classify_score(item.get("title", ""))
                events.append({
                    "code": code, "name": item.get("name", ""),
                    "event_type": scored["event_type"], "title": item.get("title", ""),
                    "source": source, "impact_score": scored["impact_score"],
                    "event_date": item.get("date") or now_beijing().date().isoformat(),
                    "url": item.get("url", ""),
                    "in_watchlist": 1 if code in watch_set else 0,
                })
        except Exception as exc:  # noqa: BLE001
            logger.info("事件源 %s 失败: %s", source, exc)
    # 去重 + 落库
    seen = set()
    rows, unique = [], []
    for e in events:
        key = _dedup_key(e["code"], e["title"], e["source"])
        if key in seen:
            continue
        seen.add(key)
        e["dedup_key"] = key
        unique.append(e)
        rows.append({**e, "created_at": now_beijing().isoformat()})
    if rows:
        db_append("event_scan", rows)
    unique.sort(key=lambda x: abs(x["impact_score"]), reverse=True)
    payload = json_safe({
        "status": "ok", "scan_date": now_beijing().date().isoformat(),
        "event_count": len(unique),
        "items": unique[:50],
        "generated_at": now_beijing().isoformat(),
    })
    write_snapshot(SNAPSHOT_NAME, payload)
    return payload


def read_latest() -> dict:
    snap = read_snapshot(SNAPSHOT_NAME)
    if snap:
        return snap
    return {"status": "empty", "items": [], "reason": "尚未扫描"}


def read_stock_events(code: str) -> list:
    df = db_query("SELECT * FROM event_scan WHERE code = :c ORDER BY id DESC LIMIT 50", {"c": str(code).zfill(6)})
    if df.empty:
        return []
    return json_safe(json.loads(df.to_json(orient="records", force_ascii=False)))
