"""去东财化首阶段回归：真实缺陷反例、单位、时效与准入。"""
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Event
from unittest.mock import Mock, patch

import pandas as pd

from backend.plugins import common
from backend.agents.layer1_data_collector.sources import historical_kline as history
from backend.agents.layer1_data_collector.sources import eastmoney_quote as quotes
from backend.agents.layer1_data_collector.sources.quote_contract import (
    BEIJING_TZ, at_limit_price, normalize_sina_bulk, parse_tencent_quotes, quote_is_current,
)
from scripts.source_verification import Checks, compare_closes, validate_bars


def bars(count=30, start="2026-01-01"):
    return pd.DataFrame({"日期": pd.date_range(start, periods=count).strftime("%Y-%m-%d"),
                         "开盘": 10., "收盘": 10., "最高": 11., "最低": 9., "成交量": 100.})


def quote_text(symbol="sh600519", stamp="20260911134627"):
    fields = [""] * 50
    for index, value in {1: "测试股票", 2: symbol[2:], 3: "1274.82", 4: "1285.13", 6: "1000",
                         30: stamp, 32: "-0.80", 33: "1290", 34: "1260", 37: "338879",
                         38: "0.21", 39: "20", 44: "100", 45: "200", 47: "1413.64",
                         48: "1156.62", 49: "1.3"}.items():
        fields[index] = value
    return f'v_{symbol}="' + "~".join(fields) + '";'


class KlineCacheTests(unittest.TestCase):
    def setUp(self):
        common.kline_cache_clear()
        self.addCleanup(common.kline_cache_clear)

    def test_short_then_long_fetches_long_window(self):
        fetch = Mock(side_effect=lambda code, days: bars(days))
        common.get_kline_cached("600000", 30, fetch)
        self.assertEqual(len(common.get_kline_cached("600000", 130, fetch)), 130)
        self.assertEqual(fetch.call_count, 2)

    def test_long_then_short_reuses_and_copies(self):
        fetch = Mock(return_value=bars(130))
        first = common.get_kline_cached("600000", 130, fetch)
        first.loc[129, "收盘"] = 999
        second = common.get_kline_cached("600000", 30, fetch)
        self.assertEqual(len(second), 30)
        self.assertEqual(second.iloc[-1]["收盘"], 10)
        fetch.assert_called_once()

    def test_failure_expires_instead_of_poisoning_day(self):
        fetch = Mock(side_effect=[None, bars()])
        with patch.object(common, "monotonic", return_value=10):
            self.assertIsNone(common.get_kline_cached("600000", 30, fetch))
        with patch.object(common, "monotonic", return_value=12):
            self.assertIsNone(common.get_kline_cached("600000", 30, fetch))
        with patch.object(common, "monotonic", return_value=16):
            self.assertEqual(len(common.get_kline_cached("600000", 30, fetch)), 30)
        self.assertEqual(fetch.call_count, 2)

    def test_success_refreshes_during_same_day(self):
        fetch = Mock(return_value=bars())
        with patch.object(common, "monotonic", return_value=10):
            common.get_kline_cached("600000", 30, fetch)
        with patch.object(common, "monotonic", return_value=56):
            common.get_kline_cached("600000", 30, fetch)
        self.assertEqual(fetch.call_count, 2)

    def test_fetchers_do_not_share_adjustment_or_source(self):
        a, b = Mock(return_value=bars()), Mock(return_value=bars())
        common.get_kline_cached("600000", 30, a)
        common.get_kline_cached("600000", 30, b)
        a.assert_called_once()
        b.assert_called_once()

    def test_same_key_concurrent_requests_coalesce(self):
        entered, release = Event(), Event()
        def load(code, days):
            entered.set()
            self.assertTrue(release.wait(2))
            return bars(days)
        fetch = Mock(side_effect=load)
        with ThreadPoolExecutor(max_workers=4) as pool:
            first = pool.submit(common.get_kline_cached, "600000", 30, fetch)
            self.assertTrue(entered.wait(2))
            rest = [pool.submit(common.get_kline_cached, "600000", 30, fetch) for _ in range(3)]
            release.set()
            for job in [first] + rest:
                self.assertEqual(len(job.result(timeout=3)), 30)
        fetch.assert_called_once()

    def test_clear_discards_in_flight_result(self):
        def load(code, days):
            common.kline_cache_clear()
            return bars(days)
        common.get_kline_cached("600000", 30, load)
        self.assertFalse(common._kline_cache)

    def test_fetcher_exception_does_not_poison_cache(self):
        fetch = Mock(side_effect=[RuntimeError("网络异常"), bars()])
        with self.assertRaises(RuntimeError):
            common.get_kline_cached("600000", 30, fetch)
        self.assertEqual(len(common.get_kline_cached("600000", 30, fetch)), 30)


class QuoteContractTests(unittest.TestCase):
    def test_bulk_units_are_endpoint_specific(self):
        row = normalize_sina_bulk([{"symbol": "sh600519", "trade": "1274.82",
                "changeratio": "-0.00802253", "turnover": "21.3098", "amount": "3388794155"}])[0]
        self.assertAlmostEqual(row["change_pct"], -0.802253)
        self.assertAlmostEqual(row["turnover_pct"], 0.213098)
        self.assertIsNone(row["source_time"])
        self.assertNotIn("main_net_inflow", row)

    def test_bulk_missing_does_not_become_zero(self):
        row = normalize_sina_bulk([{"symbol": "sz000001", "turnover": "NaN"}])[0]
        self.assertIsNone(row["turnover_pct"])
        self.assertIsNone(row["amount"])

    def test_bulk_duplicate_and_invalid_shape_fail(self):
        for payload in ({}, [], [{"symbol": "sh600519"}] * 2):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                normalize_sina_bulk(payload)

    def test_tencent_units_identity_and_time(self):
        row = parse_tencent_quotes(quote_text(), ["sh600519"])[0]
        self.assertEqual(row["成交量"], 1000)
        self.assertEqual(row["成交额"], 3388790000)
        self.assertEqual(row["总市值"], 20000000000)
        self.assertEqual(row["换手率"], .21)
        self.assertEqual(row["涨停价"], 1413.64)
        self.assertFalse(row["degraded"])
        self.assertEqual(row["source_time"], "2026-09-11T13:46:27+08:00")

    def test_tencent_does_not_adopt_unsolicited_or_wrong_code(self):
        self.assertEqual(parse_tencent_quotes(quote_text(), ["sz000001"]), [])
        wrong = quote_text().replace("测试股票~600519", "测试股票~000001")
        self.assertEqual(parse_tencent_quotes(wrong, ["sh600519"]), [])

    def test_blank_turnover_is_not_complete(self):
        row = parse_tencent_quotes(quote_text().replace("~0.21~", "~~"), ["sh600519"])[0]
        self.assertTrue(row["degraded"])
        self.assertIn("换手率", row["missing_fields"])

    def test_stale_future_and_previous_day_rejected(self):
        now = datetime(2026, 9, 11, 14, 43, tzinfo=BEIJING_TZ)
        for offset in (-86400, -60, 10):
            self.assertFalse(quote_is_current({"source_time": (now + timedelta(seconds=offset)).isoformat()}, now))
        self.assertTrue(quote_is_current({"source_time": (now - timedelta(seconds=10)).isoformat()}, now))

    def test_lunch_and_close_require_actual_session_close(self):
        stamp = "2026-09-11T11:30:00+08:00"
        self.assertTrue(quote_is_current({"source_time": stamp}, datetime(2026, 9, 11, 12, 30, tzinfo=BEIJING_TZ)))
        self.assertFalse(quote_is_current({"source_time": stamp}, datetime(2026, 9, 11, 15, 30, tzinfo=BEIJING_TZ)))

    def test_limit_price_avoids_false_positive_and_low_price_false_negative(self):
        self.assertFalse(at_limit_price("10.98", "11.00"))
        self.assertTrue(at_limit_price("1.13", "1.13"))
        for missing in (None, 0, "NaN", "Infinity", "1.131"):
            self.assertIsNone(at_limit_price("1.13", missing))

    def test_primary_complete_skips_legacy(self):
        with patch.object(quotes, "_fetch_tencent_batch", return_value=[{"代码": "600519"}]), \
                patch.object(quotes, "_fetch_legacy_batch") as legacy:
            rows = quotes._fetch_one_batch(["1.600519"])
        legacy.assert_not_called()
        self.assertEqual(len(rows), 1)

    def test_partial_quote_only_fills_missing_codes(self):
        with patch.object(quotes, "_fetch_tencent_batch", return_value=[{"代码": "600519"}]), \
                patch.object(quotes, "_fetch_legacy_batch", return_value=[{"代码": "000001"}]) as legacy:
            rows = quotes._fetch_one_batch(["1.600519", "0.000001"])
        legacy.assert_called_once_with(["0.000001"])
        self.assertEqual(len(rows), 2)

    def test_all_sources_down_does_not_trigger_recursive_retries(self):
        with patch.object(quotes, "_fetch_one_batch", side_effect=quotes.QuoteSourcesUnavailable("断网")) as fetch:
            rows, failures = quotes._fetch_batch_with_split(["1.600000"] * 60)
        self.assertEqual((rows, failures), ([], 1))
        fetch.assert_called_once()

    def test_naive_source_time_is_unknown(self):
        self.assertFalse(quote_is_current({"source_time": "2026-09-11T14:43:00"},
                                         datetime(2026, 9, 11, 14, 43, tzinfo=BEIJING_TZ)))


class VerificationTests(unittest.TestCase):
    def test_invalid_high_and_duplicate_dates_fail(self):
        a = bars()
        a.loc[0, "最高"] = 5
        self.assertFalse(validate_bars(a))
        a = bars()
        a.loc[1, "日期"] = a.loc[0, "日期"]
        self.assertFalse(validate_bars(a))

    def test_date_alignment_and_minimum_overlap(self):
        a, b = bars(), bars(start="2026-01-05")
        self.assertTrue(compare_closes(a, b)[0])
        self.assertFalse(compare_closes(a, bars(start="2027-01-01"))[0])

    def test_difference_fails_instead_of_just_printing(self):
        a, b = bars(), bars()
        a.loc[0, "收盘"] = 10.5
        self.assertFalse(compare_closes(a, b)[0])

    def test_exit_code_distinguishes_pending_from_pass(self):
        with patch("builtins.print"):
            checks = Checks()
            self.assertEqual(checks.finish(), 0)
            checks.skip("复权", "无证据")
            self.assertEqual(checks.finish(), 2)
            checks.check("字段", False)
            self.assertEqual(checks.finish(), 1)

    def test_sina_missing_fields_and_legacy_volume_units(self):
        response = Mock()
        response.json.return_value = [dict(day=f"2026-09-0{i}", open="10", close="10", high="11", low="9", volume="10000") for i in (1, 2, 3)]
        with patch("requests.get", return_value=response):
            df = history._fetch_hist_sina("600000")
        self.assertEqual(df.iloc[0]["成交量"], 100)
        self.assertTrue(df["成交额"].isna().all())
        self.assertTrue(df["换手率"].isna().all())
        self.assertEqual(df.attrs["adjustment"], "unknown")

    def test_tencent_raw_day_never_becomes_qfq(self):
        response = Mock()
        response.json.return_value = {"data": {"sh600000": {"day": [["2026-09-01", "10", "10", "11", "9", "100"]]}}}
        with patch("requests.get", return_value=response):
            self.assertIsNone(history._fetch_hist_tencent("600000"))

    def test_qfq_fallback_never_uses_unknown_sina(self):
        with patch.object(history, "_fetch_hist_eastmoney_direct", return_value=None), \
                patch.object(history, "_fetch_hist_tencent", return_value=bars()), \
                patch.object(history, "_fetch_hist_sina") as sina:
            result = history.fetch_historical_with_source("600000")
        self.assertIn("腾讯", result[1])
        sina.assert_not_called()


if __name__ == "__main__":
    unittest.main()
