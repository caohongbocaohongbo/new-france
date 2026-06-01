import unittest

import pandas as pd

from backend.api import router_watchlist
from backend.agents.layer1_data_collector.sources import historical_kline


class WatchlistDetailSourceTest(unittest.TestCase):
    def test_parse_eastmoney_kline_rows_keeps_turnover(self):
        rows = [
            "2026-05-27,23.50,25.85,25.85,23.50,238219,601122222.00,10.00,10.00,2.35,5.43",
            "2026-05-28,24.73,24.73,25.10,24.10,366759,899122222.00,3.87,-4.33,-1.12,8.35",
        ]

        df = historical_kline._parse_eastmoney_kline_rows(rows)

        self.assertEqual(list(df["日期"]), ["2026-05-27", "2026-05-28"])
        self.assertEqual(list(df["换手率"]), [5.43, 8.35])

    def test_align_metric_series_by_date_uses_only_matching_real_dates(self):
        df = pd.DataFrame(
            [
                {"日期": "20260527", "PE_TTM": -83.48},
                {"日期": "2026-05-28", "PE_TTM": -79.86},
                {"日期": "2026-05-30", "PE_TTM": -77.64},
            ]
        )

        series = router_watchlist._align_metric_series_by_date(
            ["2026-05-27", "2026-05-28", "2026-05-29"],
            df,
            "日期",
            "PE_TTM",
        )

        self.assertEqual(series, [-83.48, -79.86, None])

    def test_fill_latest_metric_sets_only_latest_point(self):
        series = [None, None, None]

        filled = router_watchlist._fill_latest_metric_value(series, 1.08)

        self.assertEqual(filled, [None, None, 1.08])
        self.assertEqual(series, [None, None, None])


if __name__ == "__main__":
    unittest.main()
