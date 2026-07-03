"""多源协调器单元测试。"""
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from backend.plugins.principal_capital.sources import multi_source as msff
from backend.plugins.principal_capital.sources.eastmoney import FundFlowFetchError

BEIJING_TZ = timezone(timedelta(hours=8))


def _df(code="600001", ratio=55):
    return pd.DataFrame([{
        "code": code, "name": "测试股", "price": 10.0, "change_pct": 2.0,
        "total_amount": 100000000, "main_net_inflow": 55000000,
        "main_inflow_ratio": ratio, "super_net": 30000000, "big_net": 25000000,
        "mid_net": 0, "small_net": -55000000, "source": "eastmoney",
    }])


def _patch_paths(tmp):
    """Patch 所有路径常量到临时目录。"""
    return [
        patch.object(msff, "DATA_DIR", Path(tmp)),
        patch.object(msff, "SOURCE_HEALTH_FILE", Path(tmp) / "health.json"),
        patch.object(msff, "_CACHE_JSON_FILE", Path(tmp) / "cache.json"),
        patch.object(msff, "CACHE_FILE", Path(tmp) / "cache.parquet"),
    ]


class MultiSourceFundFlowTest(unittest.TestCase):

    def setUp(self):
        msff._HEALTH = None

    def test_primary_source_success(self):
        with TemporaryDirectory() as tmp:
            patches = _patch_paths(tmp) + [
                patch.object(msff, "_fetch_eastmoney_any", return_value=_df()),
                patch.object(msff, "fetch_market_fund_flow_via_akshare", return_value=_df("600002")),
            ]
            for p in patches:
                p.start()
            try:
                df, status = msff.fetch_market_fund_flow_resilient()
            finally:
                for p in patches:
                    p.stop()
        self.assertEqual(status["active_source"], "eastmoney")
        self.assertEqual(df.iloc[0]["code"], "600001")

    def test_fallback_to_backup(self):
        with TemporaryDirectory() as tmp:
            patches = _patch_paths(tmp) + [
                patch.object(msff, "_fetch_eastmoney_any", side_effect=FundFlowFetchError("primary")),
                patch.object(msff, "fetch_market_fund_flow_via_akshare",
                             return_value=_df("600003")),
            ]
            for p in patches:
                p.start()
            try:
                df, status = msff.fetch_market_fund_flow_resilient()
            finally:
                for p in patches:
                    p.stop()
        self.assertEqual(status["active_source"], "akshare")
        self.assertEqual(df.iloc[0]["code"], "600003")

    def test_fallback_to_akshare(self):
        with TemporaryDirectory() as tmp:
            patches = _patch_paths(tmp) + [
                patch.object(msff, "_fetch_eastmoney_any",
                             side_effect=FundFlowFetchError("primary")),
                patch.object(msff, "fetch_market_fund_flow_via_akshare",
                             return_value=_df("600005")),
            ]
            for p in patches:
                p.start()
            try:
                df, status = msff.fetch_market_fund_flow_resilient()
            finally:
                for p in patches:
                    p.stop()
        self.assertEqual(status["active_source"], "akshare")
        self.assertEqual(df.iloc[0]["code"], "600005")

    def test_cache_used_when_all_sources_fail(self):
        with TemporaryDirectory() as tmp:
            for p in _patch_paths(tmp):
                p.start()
            try:
                msff._write_cache(_df("600006"), datetime.now(BEIJING_TZ))
                with patch.object(msff, "_fetch_eastmoney_any",
                                  side_effect=FundFlowFetchError("primary")), \
                     patch.object(msff, "fetch_market_fund_flow_via_akshare",
                                  side_effect=FundFlowFetchError("akshare")):
                    df, status = msff.fetch_market_fund_flow_resilient()
            finally:
                pass  # cleanup happens via TemporaryDirectory
        self.assertEqual(status["active_source"], "cache")
        self.assertTrue(status["is_stale"])
        self.assertEqual(df.iloc[0]["code"], "600006")

    def test_no_cache_when_expired(self):
        with TemporaryDirectory() as tmp:
            for p in _patch_paths(tmp):
                p.start()
            try:
                msff._write_cache(_df("600007"), datetime.now(BEIJING_TZ) - timedelta(hours=2))
                with patch.object(msff, "_fetch_eastmoney_any",
                                  side_effect=FundFlowFetchError("primary")), \
                     patch.object(msff, "fetch_market_fund_flow_via_akshare",
                                  side_effect=FundFlowFetchError("akshare")):
                    df, status = msff.fetch_market_fund_flow_resilient(cache_ttl_seconds=60)
            finally:
                pass
        self.assertTrue(df.empty)
        self.assertEqual(status["active_source"], "none")

    def test_circuit_breaker_skips_after_five_failures(self):
        with TemporaryDirectory() as tmp:
            for p in _patch_paths(tmp):
                p.start()
            try:
                with patch.object(msff, "_fetch_eastmoney_any",
                                  side_effect=[FundFlowFetchError("a"), FundFlowFetchError("b"),
                                               FundFlowFetchError("c"), FundFlowFetchError("d"),
                                               FundFlowFetchError("e"), _df("600008")]), \
                     patch.object(msff, "fetch_market_fund_flow_via_akshare",
                                  side_effect=FundFlowFetchError("akshare")):
                    msff.fetch_market_fund_flow_resilient(cache_ttl_seconds=1)
                    msff.fetch_market_fund_flow_resilient(cache_ttl_seconds=1)
                    msff.fetch_market_fund_flow_resilient(cache_ttl_seconds=1)
                    msff.fetch_market_fund_flow_resilient(cache_ttl_seconds=1)
                    msff.fetch_market_fund_flow_resilient(cache_ttl_seconds=1)
                    _, status = msff.fetch_market_fund_flow_resilient(cache_ttl_seconds=1)
            finally:
                pass
        first_attempt = status["attempts"][0]
        self.assertEqual(first_attempt["status"], "skipped_blocked")


if __name__ == "__main__":
    unittest.main()
