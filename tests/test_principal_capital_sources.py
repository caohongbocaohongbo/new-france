"""主力资金数据源冗余优化测试。"""
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pandas as pd
import requests

from backend.plugins.principal_capital.sources import multi_source as msff
from backend.plugins.principal_capital.sources.tencent import (
    _prefix,
    fetch_single_stock_fund_flow_tencent,
)

BEIJING_TZ = timezone(timedelta(hours=8))


def _df(code="600001"):
    return pd.DataFrame([{
        "code": code,
        "name": "测试股",
        "price": 10.0,
        "change_pct": 2.0,
        "total_amount": 100000000,
        "main_net_inflow": 55000000,
        "main_inflow_ratio": 55,
        "super_net": 30000000,
        "big_net": 25000000,
        "mid_net": 0,
        "small_net": -55000000,
        "source": "eastmoney",
    }])


def _patch_paths(tmp):
    return [
        patch.object(msff, "DATA_DIR", Path(tmp)),
        patch.object(msff, "SOURCE_HEALTH_FILE", Path(tmp) / "health.json"),
        patch.object(msff, "_CACHE_JSON_FILE", Path(tmp) / "cache.json"),
        patch.object(msff, "CACHE_FILE", Path(tmp) / "cache.parquet"),
    ]


class TencentSourceTest(unittest.TestCase):
    def test_prefix_maps_shanghai_and_shenzhen(self):
        self.assertEqual(_prefix("600000"), "sh")
        self.assertEqual(_prefix("900901"), "sh")
        self.assertEqual(_prefix("000001"), "sz")

    def test_single_stock_parses_and_normalizes_units(self):
        session = Mock()
        response = Mock()
        response.raise_for_status.return_value = None
        response.text = (
            'v_ff_sh600000="600000~12.5~2.5~10~0~40~10~0~0~100~0~0~测试股~2026-07-03";'
        )
        session.get.return_value = response

        result = fetch_single_stock_fund_flow_tencent("600000", session=session)

        self.assertEqual(result["code"], "600000")
        self.assertEqual(result["name"], "测试股")
        self.assertEqual(result["main_net_inflow"], 100000.0)
        self.assertEqual(result["main_inflow_ratio"], 10.0)
        self.assertEqual(result["source"], "tencent")

    def test_single_stock_error_returns_none(self):
        session = Mock()
        session.get.side_effect = requests.Timeout("timeout")
        self.assertIsNone(fetch_single_stock_fund_flow_tencent("600000", session=session))


class MultiSourceResilienceTest(unittest.TestCase):
    def setUp(self):
        msff._HEALTH = None

    def test_fetch_eastmoney_any_falls_back_to_next_host(self):
        with patch.object(msff, "EASTMONEY_HOSTS", ["host-a", "host-b"]), patch.object(
            msff,
            "fetch_market_fund_flow",
            side_effect=[RuntimeError("boom"), _df("600010")],
        ) as mocked_fetch:
            df = msff._fetch_eastmoney_any()

        self.assertEqual(df.iloc[0]["code"], "600010")
        self.assertEqual(mocked_fetch.call_args_list[0].kwargs["base_url"], "host-a")
        self.assertEqual(mocked_fetch.call_args_list[1].kwargs["base_url"], "host-b")

    def test_fetch_eastmoney_any_retries_next_host_when_first_returns_empty_dataframe(self):
        with patch.object(msff, "EASTMONEY_HOSTS", ["host-a", "host-b"]), patch.object(
            msff,
            "fetch_market_fund_flow",
            side_effect=[pd.DataFrame(), _df("600011")],
        ) as mocked_fetch:
            df = msff._fetch_eastmoney_any()

        self.assertEqual(df.iloc[0]["code"], "600011")
        self.assertEqual(mocked_fetch.call_args_list[0].kwargs["base_url"], "host-a")
        self.assertEqual(mocked_fetch.call_args_list[1].kwargs["base_url"], "host-b")

    def test_circuit_breaker_blocks_on_fifth_failure(self):
        with TemporaryDirectory() as tmp:
            patches = _patch_paths(tmp)
            for item in patches:
                item.start()
            try:
                baseline = datetime(2026, 7, 3, 15, 0, tzinfo=BEIJING_TZ)
                with patch.object(msff, "_now", return_value=baseline):
                    for _ in range(4):
                        msff._mark_failure("eastmoney")
                    health = msff._load_health()
                    self.assertIsNone(health["eastmoney"]["blocked_until"])

                    msff._mark_failure("eastmoney")
                    health = msff._load_health()
                    self.assertEqual(health["eastmoney"]["failure_streak"], 5)
                    blocked_until = datetime.fromisoformat(health["eastmoney"]["blocked_until"])
            finally:
                for item in reversed(patches):
                    item.stop()

        self.assertLessEqual(blocked_until - baseline, timedelta(minutes=8))


if __name__ == "__main__":
    unittest.main()
