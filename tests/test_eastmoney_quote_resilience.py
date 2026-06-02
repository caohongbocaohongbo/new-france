import unittest
import sys
import types
from unittest.mock import patch

from backend.agents.layer1_data_collector.sources import eastmoney_quote


class EastmoneyQuoteResilienceTest(unittest.TestCase):
    def test_parse_response_treats_empty_payload_as_no_rows(self):
        self.assertEqual(eastmoney_quote._parse_response({"data": None}), [])
        self.assertEqual(eastmoney_quote._parse_response({}), [])

    def test_fetch_one_batch_falls_back_to_sina_when_eastmoney_is_empty(self):
        class FakeResponse:
            encoding = "utf-8"

            def __init__(self, json_data=None, text=""):
                self._json_data = json_data
                self.text = text

            def raise_for_status(self):
                return None

            def json(self):
                return self._json_data

        def fake_requests_get(url, **kwargs):
            if "hq.sinajs.cn" in url:
                return FakeResponse(text=(
                    'var hq_str_sz000001="平安银行,10.00,10.00,10.50,10.80,9.90,'
                    '10.49,10.50,1000000,10500000.00,0,0,0,0,0,0,0,0,0,0,0,0,0,0,'
                    '0,0,0,0,0,0,2026-06-02,15:00:00,00";'
                ))
            return FakeResponse(json_data={"data": None})

        def fake_curl_get(*args, **kwargs):
            raise RuntimeError("curl blocked")

        fake_requests = types.SimpleNamespace(get=fake_requests_get)
        fake_curl = types.SimpleNamespace(requests=types.SimpleNamespace(get=fake_curl_get))

        with patch.dict(sys.modules, {"requests": fake_requests, "curl_cffi": fake_curl}), \
                patch.object(eastmoney_quote.logger, "warning"):
            rows = eastmoney_quote._fetch_one_batch(["0.000001"])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["代码"], "000001")
        self.assertEqual(rows[0]["名称"], "平安银行")
        self.assertEqual(rows[0]["最新价"], 10.5)
        self.assertEqual(rows[0]["涨跌幅"], 5.0)
        self.assertIsNone(rows[0]["市盈率"])

    def test_fetch_stock_quotes_splits_failed_large_batches(self):
        original_fetch = eastmoney_quote._fetch_one_batch
        original_batch_size = eastmoney_quote.BATCH_SIZE
        original_min_batch_size = eastmoney_quote.MIN_SPLIT_BATCH_SIZE
        original_delay = eastmoney_quote.BATCH_DELAY_SECONDS
        eastmoney_quote._QUOTE_CACHE.clear()

        def fake_fetch(secids_batch):
            if len(secids_batch) > 2:
                raise RuntimeError("rate limited")
            rows = []
            for secid in secids_batch:
                code = secid.split(".", 1)[1]
                rows.append({
                    "代码": code,
                    "名称": f"股票{code}",
                    "最新价": 10.0,
                    "涨跌幅": 1.0,
                    "成交量": 1000.0,
                    "成交额": 10000.0,
                    "换手率": 2.0,
                    "市盈率": 20.0,
                    "量比": 1.2,
                    "总市值": 100000000.0,
                    "流通市值": 80000000.0,
                })
            return rows

        try:
            eastmoney_quote._fetch_one_batch = fake_fetch
            eastmoney_quote.BATCH_SIZE = 4
            eastmoney_quote.MIN_SPLIT_BATCH_SIZE = 2
            eastmoney_quote.BATCH_DELAY_SECONDS = 0

            with patch.object(eastmoney_quote.logger, "warning"):
                df = eastmoney_quote.fetch_stock_quotes(["000001", "000002", "000003", "000004"])

            self.assertEqual(set(df["代码"].tolist()), {"000001", "000002", "000003", "000004"})
            self.assertEqual(len(df), 4)
        finally:
            eastmoney_quote._fetch_one_batch = original_fetch
            eastmoney_quote.BATCH_SIZE = original_batch_size
            eastmoney_quote.MIN_SPLIT_BATCH_SIZE = original_min_batch_size
            eastmoney_quote.BATCH_DELAY_SECONDS = original_delay
            eastmoney_quote._QUOTE_CACHE.clear()


if __name__ == "__main__":
    unittest.main()
