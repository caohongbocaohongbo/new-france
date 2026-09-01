"""交易日历基础设施单测（纯本地，不联网）。"""
from datetime import date, datetime, timedelta, timezone

import pytest

from backend.services import trading_calendar as tc


BEIJING_TZ = timezone(timedelta(hours=8))

# 合成日历：2025-09-30 与 2025-10-09 之间（10-01~10-08 休市）
SYNTHETIC = {
    "2025-09-26", "2025-09-29", "2025-09-30",
    "2025-10-09", "2025-10-10",
}


@pytest.fixture
def synthetic_calendar(monkeypatch):
    monkeypatch.setattr(tc, "_CACHE", None)
    monkeypatch.setattr(tc, "_load", lambda: {
        "dates": set(SYNTHETIC), "source": "test", "fetched_at": None, "degraded": False,
    })
    return SYNTHETIC


def test_is_trading_day_with_synthetic(synthetic_calendar):
    assert tc.is_trading_day(date(2025, 9, 30)) is True
    assert tc.is_trading_day(date(2025, 10, 1)) is False   # 国庆休市
    assert tc.is_trading_day(date(2025, 10, 9)) is True


def test_next_prev_trading_day_jumps_over_holiday(synthetic_calendar):
    assert tc.next_trading_day(date(2025, 9, 30)) == date(2025, 10, 9)
    assert tc.prev_trading_day(date(2025, 10, 9)) == date(2025, 9, 30)


def test_n_trading_days_later_equals_next(synthetic_calendar):
    assert tc.n_trading_days_later(date(2025, 9, 30), 1) == date(2025, 10, 9)
    assert tc.n_trading_days_later(date(2025, 9, 30), 2) == date(2025, 10, 10)


def test_next_trading_day_n_zero_returns_self(synthetic_calendar):
    # n=0 边界：返回该日本身（不能往反方向找一天）
    assert tc.next_trading_day(date(2025, 9, 30), 0) == date(2025, 9, 30)
    assert tc.n_trading_days_later(date(2025, 9, 30), 0) == date(2025, 9, 30)


def test_prev_trading_day_n_zero_returns_self(synthetic_calendar):
    assert tc.prev_trading_day(date(2025, 10, 9), 0) == date(2025, 10, 9)
    assert tc.prev_trading_day(date(2025, 10, 1), 0) == date(2025, 10, 1)  # 非交易日也返回本身


def test_trading_days_between_counts(synthetic_calendar):
    assert tc.trading_days_between(date(2025, 9, 30), date(2025, 10, 9)) == 2


def test_accepts_datetime_and_str(synthetic_calendar):
    assert tc.is_trading_day(datetime(2025, 9, 30, 9, 30, tzinfo=BEIJING_TZ)) is True
    assert tc.is_trading_day("2025-10-01") is False


def test_invalid_input(synthetic_calendar):
    assert tc.is_trading_day(None) is False
    assert tc.is_trading_day("not-a-date") is False
    assert tc.next_trading_day("bad") is None
    assert tc.prev_trading_day("bad") is None


def test_degraded_fallback_never_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(tc, "_CACHE", None)
    monkeypatch.setattr(tc, "CALENDAR_FILE", tmp_path / "missing.json")
    assert tc.is_trading_day(date(2026, 6, 8)) is True   # 周一
    assert tc.is_trading_day(date(2026, 6, 7)) is False  # 周日
    assert tc.calendar_status()["degraded"] is True


def test_refresh_returns_ok_when_cached(synthetic_calendar):
    result = tc.refresh()
    assert result["ok"] is True
    assert result["refreshed"] is False


def test_real_calendar_file_if_present():
    payload = tc._read_calendar_file()
    if payload is None:
        pytest.skip("data/trading_calendar.json 不存在，跳过真实日历校验")
    dates = set(str(d)[:10] for d in payload["dates"])
    assert "2025-09-30" in dates
    assert "2025-10-01" not in dates
    assert "2026-06-08" in dates

