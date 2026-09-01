"""A 股交易时段判定。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

BEIJING_TZ = timezone(timedelta(hours=8))


def current_trading_session(now: Optional[datetime] = None) -> str:
    """返回 open | closed | pre | post。"""
    current = now or datetime.now(BEIJING_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=BEIJING_TZ)
    else:
        current = current.astimezone(BEIJING_TZ)

    # 接入交易日历，精确识别法定节假日（非交易日一律 closed）。
    from ..trading_calendar import is_trading_day

    if not is_trading_day(current.date()):
        return "closed"

    minutes = current.hour * 60 + current.minute
    if minutes < 9 * 60 + 30:
        return "pre"
    if 9 * 60 + 30 <= minutes < 11 * 60 + 30:
        return "open"
    if 11 * 60 + 30 <= minutes < 13 * 60:
        return "closed"
    if 13 * 60 <= minutes < 15 * 60:
        return "open"
    return "post"

