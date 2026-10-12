"""26 舆情热度聚合编排服务：多源聚合打分 + 排行 + 监控列表交叉。"""
import json
import logging

from backend.plugins.common import (
    BEIJING_TZ, db_append, db_query, json_safe, now_beijing, read_snapshot, write_snapshot,
)

from .config import SNAPSHOT_NAME
from .sources import ALL_SOURCES

logger = logging.getLogger(__name__)

MAIN_BOARD = ("600", "601", "603", "605", "000", "001", "002", "003")


def _in_watchlist(codes: set) -> dict:
    """从监控列表取主板代码集合。"""
    try:
        from backend.services.watchlist_store import parse_watchlist
        items = parse_watchlist()
        return {c for c in items if str(c).zfill(6).startswith(MAIN_BOARD)}
    except Exception:  # noqa: BLE001
        return set()


def run_scan_once() -> dict:
    """聚合全部源，按总分排序，写快照 + SQLite。"""
    merged = {}
    for name, fn in ALL_SOURCES.items():
        try:
            for item in fn():
                code = str(item.get("code", "")).zfill(6)
                if not code.startswith(MAIN_BOARD):
                    continue
                entry = merged.setdefault(code, {"code": code, "name": item.get("name", ""), "score": 0, "sources": {}})
                entry["score"] += int(item.get("score", 1))
                entry["sources"][name] = entry["sources"].get(name, 0) + int(item.get("score", 1))
                if not entry["name"] and item.get("name"):
                    entry["name"] = item["name"]
        except Exception as exc:  # noqa: BLE001
            logger.info("舆情源 %s 执行失败: %s", name, exc)
    ranked = sorted(merged.values(), key=lambda x: x["score"], reverse=True)
    watch_set = _in_watchlist({r["code"] for r in ranked})
    scan_date = now_beijing().date().isoformat()
    rows = []
    for i, r in enumerate(ranked[:100], 1):
        r["rank"] = i
        r["in_watchlist"] = 1 if r["code"] in watch_set else 0
        rows.append({"scan_date": scan_date, "code": r["code"], "name": r["name"],
                     "score": r["score"], "sources_json": json.dumps(r["sources"], ensure_ascii=False),
                     "rank": i, "in_watchlist": r["in_watchlist"], "created_at": now_beijing().isoformat()})
    if rows:
        db_append("hot_scan", rows)
    payload = json_safe({
        "status": "ok", "scan_date": scan_date,
        "source_count": len(ALL_SOURCES),
        "item_count": len(ranked),
        "items": ranked[:50],
        "generated_at": now_beijing().isoformat(),
    })
    write_snapshot(SNAPSHOT_NAME, payload)
    return payload


def read_latest() -> dict:
    snap = read_snapshot(SNAPSHOT_NAME)
    if snap:
        return snap
    return {"status": "empty", "items": [], "reason": "尚未扫描"}


def read_history(days: int = 30) -> list:
    df = db_query("SELECT * FROM hot_scan ORDER BY id DESC LIMIT :n", {"n": int(days) * 100})
    if df.empty:
        return []
    return json_safe(json.loads(df.to_json(orient="records", force_ascii=False)))
