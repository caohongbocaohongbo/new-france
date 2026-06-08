from datetime import datetime, timedelta, timezone
import unittest

from backend.api.router_system import trading_session_status


BEIJING_TZ = timezone(timedelta(hours=8))


class SystemStatusTest(unittest.TestCase):
    def test_trading_session_splits_lunch_break_from_active_trading(self):
        cases = [
            (datetime(2026, 6, 8, 9, 29, tzinfo=BEIJING_TZ), False, "pre_open", "未开盘"),
            (datetime(2026, 6, 8, 9, 30, tzinfo=BEIJING_TZ), True, "trading", "交易中"),
            (datetime(2026, 6, 8, 11, 30, tzinfo=BEIJING_TZ), False, "midday_break", "休市中"),
            (datetime(2026, 6, 8, 13, 0, tzinfo=BEIJING_TZ), True, "trading", "交易中"),
            (datetime(2026, 6, 8, 15, 0, tzinfo=BEIJING_TZ), False, "closed", "已收盘"),
            (datetime(2026, 6, 7, 10, 0, tzinfo=BEIJING_TZ), False, "non_trading_day", "休市中"),
        ]

        for current, is_trading, session, text in cases:
            with self.subTest(current=current):
                status = trading_session_status(current)
                self.assertEqual(status["is_trading_hours"], is_trading)
                self.assertEqual(status["market_session"], session)
                self.assertEqual(status["market_status_text"], text)


if __name__ == "__main__":
    unittest.main()
