from datetime import datetime, timedelta, timezone

from backend.services.data_backend.trading_session import current_trading_session


BEIJING_TZ = timezone(timedelta(hours=8))


def test_current_trading_session_returns_expected_labels():
    cases = [
        (datetime(2026, 7, 6, 9, 29, tzinfo=BEIJING_TZ), "pre"),
        (datetime(2026, 7, 6, 9, 30, tzinfo=BEIJING_TZ), "open"),
        (datetime(2026, 7, 6, 12, 0, tzinfo=BEIJING_TZ), "closed"),
        (datetime(2026, 7, 6, 14, 0, tzinfo=BEIJING_TZ), "open"),
        (datetime(2026, 7, 6, 15, 1, tzinfo=BEIJING_TZ), "post"),
        (datetime(2026, 7, 4, 10, 0, tzinfo=BEIJING_TZ), "closed"),
    ]

    for current, expected in cases:
        assert current_trading_session(current) == expected

