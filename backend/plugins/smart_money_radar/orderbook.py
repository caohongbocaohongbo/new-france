"""07 盘口委托失衡与五档动态雷达（事件判定 + 快照，复用 RadarStore.quotes）。"""
import json
import logging
from datetime import datetime
from pathlib import Path

from .config import BEIJING_TZ, CONFIG, DATA_DIR, REPORT_DIR
from .indicators import (
    _float, ask_pressure_decay, bid_ask_imbalance, bid_buildup, bid_buildup_signal,
    top_concentration, withdraw_pressure_signal,
)

logger = logging.getLogger(__name__)
ORDERBOOK_LATEST = REPORT_DIR / "orderbook_latest.json"
EVENTS_DIR = DATA_DIR


def _series(quotes, field):
    """从 quotes 滑动窗口提取某字段的时间序列。"""
    return [_float((row.get("quote") or {}).get(field), 0) or 0 for row in quotes or []]


def evaluate_orderbook(code: str, quotes: list, now: datetime, cfg: dict = None) -> dict:
    """计算单票盘口指标并判定事件。"""
    cfg = cfg or CONFIG
    latest = (quotes[-1].get("quote") or {}) if quotes else {}
    ask1 = _series(quotes, "ask_vol1")
    bid1 = _series(quotes, "bid_vol1")
    prices = _series(quotes, "price")
    events = []
    if withdraw_pressure_signal(ask1, prices, float(cfg.get("orderbook_withdraw_drop", 0.4))):
        events.append("疑似撤压")
    if bid_buildup_signal(bid1, float(cfg.get("orderbook_bid_rise", 0.5))):
        events.append("疑似垫单抢筹")
    return {
        "code": str(code).zfill(6),
        "name": latest.get("name") or "",
        "price": _float(latest.get("price")),
        "imbalance": bid_ask_imbalance(latest),
        "concentration": top_concentration(latest),
        "ask_decay": ask_pressure_decay(ask1),
        "bid_buildup": bid_buildup(bid1),
        "events": events,
    }


def evaluate_pool(store, now: datetime, cfg: dict = None) -> list:
    """对观察池全量计算盘口指标并排序。"""
    cfg = cfg or CONFIG
    results = []
    for code, quotes in (getattr(store, "quotes", {}) or {}).items():
        if not quotes:
            continue
        results.append(evaluate_orderbook(code, list(quotes), now, cfg))
    results.sort(key=lambda r: abs(r.get("imbalance") or 0), reverse=True)
    return results


def write_orderbook_events(rows: list, now: datetime) -> Path:
    """触发事件写 data/orderbook_events_{date}.json（只落本地）。"""
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = EVENTS_DIR / f"orderbook_events_{now.date().isoformat()}.json"
    events = [r for r in rows if r.get("events")]
    path.write_text(json.dumps({"date": now.date().isoformat(), "events": events}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_orderbook_latest(payload: dict) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ORDERBOOK_LATEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_orderbook_latest() -> dict:
    if not ORDERBOOK_LATEST.exists():
        return {"status": "not_run", "items": []}
    try:
        return json.loads(ORDERBOOK_LATEST.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "error", "items": []}
