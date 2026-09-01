"""交易日历基础设施（全局地基）。

接入权威交易日历（akShare tool_trade_date_hist_sina，交易所口径），
供全局判断：是否交易日 / 前后第 N 个交易日 / 区间交易日数 / T+N 标签。

设计原则：
- 模块级内存缓存 + 文件缓存双层，调用零网络开销；
- 首次拉取后缓存 data/trading_calendar.json（含 dates/fetched_at/source/degraded）；
- 网络失败且缓存缺失时，回退 weekday()<5 并标 degraded=True，绝不因日历拉取失败崩调度；
- 时区统一 BEIJING_TZ；外部调用带 timeout。
"""
from __future__ import annotations

import json
import logging
from bisect import bisect_left, bisect_right
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)

BEIJING_TZ = timezone(timedelta(hours=8))
PROJECT_DIR = Path(__file__).resolve().parents[2]
CALENDAR_FILE = PROJECT_DIR / "data" / "trading_calendar.json"

# 模块级内存缓存
_CACHE: Optional[dict] = None


def _parse_date(value) -> Optional[date]:
    """把 date / datetime / ISO 字符串统一为 date。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _read_calendar_file() -> Optional[dict]:
    if not CALENDAR_FILE.exists():
        return None
    try:
        payload = json.loads(CALENDAR_FILE.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("dates"), list):
            return payload
    except (json.JSONDecodeError, OSError):
        return None
    return None


def _load() -> dict:
    """返回 dates/source/fetched_at/degraded；缓存缺失时 weekday 降级。"""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    payload = _read_calendar_file()
    if payload:
        dates = {str(d)[:10] for d in payload.get("dates") or [] if str(d).strip()}
        _CACHE = {
            "dates": dates,
            "source": payload.get("source") or "cache",
            "fetched_at": payload.get("fetched_at"),
            "degraded": bool(payload.get("degraded", False)),
        }
    else:
        _CACHE = {
            "dates": set(),
            "source": "weekday_fallback",
            "fetched_at": None,
            "degraded": True,
        }
    return _CACHE


def _weekday_fallback(d: date) -> bool:
    return d.weekday() < 5


def calendar_status() -> dict:
    """暴露日历来源/是否降级，供页面与快照标注 active_source/degraded。"""
    state = _load()
    return {
        "source": state["source"],
        "fetched_at": state["fetched_at"],
        "degraded": state["degraded"],
        "covered_days": len(state["dates"]),
    }


def refresh(force: bool = False) -> dict:
    """从 akShare 拉取权威交易日历并写缓存；失败保留旧缓存/降级，不抛异常。"""
    state = _load()
    if not force and state["dates"] and not state["degraded"]:
        return {"ok": True, "source": state["source"], "degraded": state["degraded"], "refreshed": False}
    try:
        import akshare as ak

        df = ak.tool_trade_date_hist_sina()
        dates = sorted(str(d)[:10] for d in df["trade_date"].tolist())
        if not dates:
            raise ValueError("akShare 交易日历为空")
    except Exception as exc:  # noqa: BLE001 网络/依赖异常都视为刷新失败
        logger.warning("交易日历刷新失败，保持降级: %s", exc)
        return {"ok": False, "source": state["source"], "degraded": True, "error": str(exc), "refreshed": False}

    payload = {
        "source": "akshare_tool_trade_date_hist_sina",
        "fetched_at": datetime.now(BEIJING_TZ).isoformat(),
        "dates": dates,
        "degraded": False,
    }
    CALENDAR_FILE.parent.mkdir(parents=True, exist_ok=True)
    CALENDAR_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    global _CACHE
    _CACHE = None
    return {"ok": True, "source": payload["source"], "degraded": False, "refreshed": True, "covered_days": len(dates)}


def _sorted_dates() -> List[str]:
    return sorted(_load()["dates"])


def is_trading_day(d) -> bool:
    """某日是否交易日（含节假日/调休，交易所口径）。"""
    parsed = _parse_date(d)
    if parsed is None:
        return False
    state = _load()
    if state["dates"]:
        return parsed.isoformat() in state["dates"]
    return _weekday_fallback(parsed)


def next_trading_day(d, n: int = 1) -> Optional[date]:
    """向后第 n 个交易日（n>=0）。日历缺失时按自然日+weekday 近似。"""
    parsed = _parse_date(d)
    if parsed is None:
        return None
    step = max(0, int(n))
    if step == 0:
        return parsed
    dates = _sorted_dates()
    if dates:
        idx = bisect_right(dates, parsed.isoformat()) + step - 1
        if idx < len(dates):
            return date.fromisoformat(dates[idx])
        return None
    cursor = parsed
    found = 0
    while found < step:
        cursor += timedelta(days=1)
        if _weekday_fallback(cursor):
            found += 1
    return cursor


def prev_trading_day(d, n: int = 1) -> Optional[date]:
    """向前第 n 个交易日（n>=0）。日历缺失时按自然日+weekday 近似。"""
    parsed = _parse_date(d)
    if parsed is None:
        return None
    step = max(0, int(n))
    if step == 0:
        return parsed
    dates = _sorted_dates()
    if dates:
        idx = bisect_left(dates, parsed.isoformat()) - step
        if idx >= 0:
            return date.fromisoformat(dates[idx])
        return None
    cursor = parsed
    found = 0
    while found < step:
        cursor -= timedelta(days=1)
        if _weekday_fallback(cursor):
            found += 1
    return cursor


def n_trading_days_later(d, n: int = 1) -> Optional[date]:
    """T+N 标签口径（供 01/03 收益标签用）。"""
    return next_trading_day(d, n)


def trading_days_between(start, end) -> int:
    """闭区间 [start, end] 内的交易日数（替代自然日+缓冲硬凑）。"""
    s = _parse_date(start)
    e = _parse_date(end)
    if s is None or e is None or e < s:
        return 0
    dates = _sorted_dates()
    if dates:
        left = bisect_left(dates, s.isoformat())
        right = bisect_right(dates, e.isoformat())
        return max(0, right - left)
    count = 0
    cursor = s
    while cursor <= e:
        if _weekday_fallback(cursor):
            count += 1
        cursor += timedelta(days=1)
    return count


def trading_days_between_dates(start: date, end: date) -> Iterable[date]:
    """闭区间内的交易日列表（供回补循环用）。"""
    s = _parse_date(start)
    e = _parse_date(end)
    if s is None or e is None or e < s:
        return []
    dates = _sorted_dates()
    if dates:
        left = bisect_left(dates, s.isoformat())
        right = bisect_right(dates, e.isoformat())
        return [date.fromisoformat(x) for x in dates[left:right]]
    result = []
    cursor = s
    while cursor <= e:
        if _weekday_fallback(cursor):
            result.append(cursor)
        cursor += timedelta(days=1)
    return result
