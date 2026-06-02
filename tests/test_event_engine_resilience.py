import sys
import types
import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from backend.events.engine import EventEngine


class EventEngineResilienceTest(unittest.IsolatedAsyncioTestCase):
    async def test_earnings_forecast_handles_missing_change_column(self):
        fake_akshare = types.SimpleNamespace(
            stock_yjyg_em=lambda date: pd.DataFrame([{
                "股票代码": "000001",
                "股票简称": "平安银行",
                "预告类型": "预增",
            }])
        )

        with patch.dict(sys.modules, {"akshare": fake_akshare}):
            events = await EventEngine()._fetch_earnings_forecast(date(2026, 6, 2))

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["related_codes"], ["000001"])
        self.assertIn("业绩预告", events[0]["title"])

    async def test_earnings_forecast_handles_none_dataframe(self):
        fake_akshare = types.SimpleNamespace(stock_yjyg_em=lambda date: None)

        with patch.dict(sys.modules, {"akshare": fake_akshare}):
            events = await EventEngine()._fetch_earnings_forecast(date(2026, 6, 2))

        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
