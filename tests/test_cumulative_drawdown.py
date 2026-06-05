import unittest
from datetime import date

import pandas as pd

from backend.api import router_watchlist
from backend.agents.layer4_audit.validators import validate_drop_consistency
from backend.services import screening_service


class CumulativeDrawdownTest(unittest.TestCase):
    def test_screening_price_history_uses_ref_price_drawdown_from_watch_date(self):
        hist = pd.DataFrame(
            [
                {"日期": "2026-05-19", "收盘": 51.00, "涨跌幅": 1.00},
                {"日期": "2026-05-20", "收盘": 53.25, "涨跌幅": 10.00},
                {"日期": "2026-05-21", "收盘": 52.58, "涨跌幅": -1.26},
                {"日期": "2026-05-22", "收盘": 49.82, "涨跌幅": -5.25},
                {"日期": "2026-05-25", "收盘": 53.73, "涨跌幅": 7.85},
                {"日期": "2026-05-26", "收盘": 53.01, "涨跌幅": -1.34},
                {"日期": "2026-05-27", "收盘": 49.76, "涨跌幅": -6.13},
                {"日期": "2026-05-28", "收盘": 49.95, "涨跌幅": 0.38},
                {"日期": "2026-05-29", "收盘": 48.03, "涨跌幅": -3.84},
                {"日期": "2026-06-01", "收盘": 45.75, "涨跌幅": -4.75},
                {"日期": "2026-06-02", "收盘": 46.00, "涨跌幅": 0.55},
                {"日期": "2026-06-03", "收盘": 49.22, "涨跌幅": 7.00},
            ]
        )

        series = screening_service._build_price_history_series(
            hist,
            "2026-05-20",
            50.60,
            current_price=45.81,
            current_date=date(2026, 6, 4),
        )

        self.assertEqual(series[0]["date"], "2026-05-20")
        self.assertEqual(series[-1]["date"], "2026-06-04")
        self.assertEqual(series[-1]["drawdown_pct"], -9.47)
        self.assertNotEqual(series[-1]["drawdown_pct"], -20.33)

    def test_watchlist_current_drawdown_uses_latest_ref_price_drawdown(self):
        hist = pd.DataFrame(
            [
                {"日期": "2026-05-20", "收盘": 53.25, "涨跌幅": 10.00},
                {"日期": "2026-05-21", "收盘": 52.58, "涨跌幅": -1.26},
                {"日期": "2026-05-22", "收盘": 49.82, "涨跌幅": -5.25},
                {"日期": "2026-05-25", "收盘": 53.73, "涨跌幅": 7.85},
                {"日期": "2026-05-26", "收盘": 53.01, "涨跌幅": -1.34},
                {"日期": "2026-05-27", "收盘": 49.76, "涨跌幅": -6.13},
                {"日期": "2026-05-28", "收盘": 49.95, "涨跌幅": 0.38},
                {"日期": "2026-05-29", "收盘": 48.03, "涨跌幅": -3.84},
                {"日期": "2026-06-01", "收盘": 45.75, "涨跌幅": -4.75},
                {"日期": "2026-06-02", "收盘": 46.00, "涨跌幅": 0.55},
                {"日期": "2026-06-03", "收盘": 49.22, "涨跌幅": 7.00},
                {"日期": "2026-06-04", "收盘": 45.81, "涨跌幅": 0.00},
            ]
        )

        value = router_watchlist._compute_watchlist_drop_pct(
            hist,
            watch_date="2026-05-20",
            ref_price=50.60,
            current_price=45.81,
        )

        self.assertEqual(value, -9.47)

    def test_ref_price_drawdown_does_not_sum_daily_offsets(self):
        hist = pd.DataFrame(
            [
                {"日期": "2026-06-01", "收盘": 3.01, "涨跌幅": 9.85},
                {"日期": "2026-06-02", "收盘": 2.98, "涨跌幅": -1.00},
                {"日期": "2026-06-03", "收盘": 2.92, "涨跌幅": -2.01},
                {"日期": "2026-06-04", "收盘": 2.90, "涨跌幅": -0.68},
                {"日期": "2026-06-05", "收盘": 3.00, "涨跌幅": 3.45},
            ]
        )

        series = screening_service._build_price_history_series(
            hist,
            "2026-06-01",
            3.01,
        )

        self.assertEqual(
            [(row["date"], row["drawdown_pct"]) for row in series],
            [
                ("2026-06-01", 0.0),
                ("2026-06-02", -1.0),
                ("2026-06-03", -2.99),
                ("2026-06-04", -3.65),
                ("2026-06-05", -0.33),
            ],
        )
        self.assertNotEqual(series[-1]["drawdown_pct"], -7.97)

    def test_audit_accepts_reported_cumulative_drawdown_from_price_history(self):
        result = validate_drop_consistency({
            "current_price": 45.81,
            "ref_price": 50.60,
            "drop_pct": -9.47,
            "price_history": [
                {"date": "2026-06-03", "drawdown_pct": -2.73},
                {"date": "2026-06-04", "drawdown_pct": -9.47},
            ],
        })

        self.assertEqual(result.status, "pass")


if __name__ == "__main__":
    unittest.main()
