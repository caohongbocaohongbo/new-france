"""sina_market 全主板数据源单元测试。"""
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.plugins.principal_capital.sources import sina_market as sm
from backend.plugins.principal_capital.sources.eastmoney import FundFlowFetchError


class SinaMarketTest(unittest.TestCase):

    def test_main_board_prefix_filter(self):
        # 主板：沪市 60；深市 000/001/002/003
        for code in ("600000", "000001", "001979", "002415", "003816"):
            self.assertTrue(sm._is_main_board_code(code), code)
        # 排除：创业板 300/301、科创板 688、北交所 920/430/8x
        for code in ("300750", "301029", "688981", "920819", "430047", "830799"):
            self.assertFalse(sm._is_main_board_code(code), code)

    def test_fetch_codes_from_node_filters_main_board(self):
        page1 = [
            {"code": "600000", "symbol": "sh600000"},
            {"code": "300750", "symbol": "sz300750"},   # 创业板，排除
            {"code": "002415", "symbol": "sz002415"},   # 深主板，保留
            {"code": "688981", "symbol": "sh688981"},   # 科创板，排除
        ]
        with patch.object(sm.requests, "Session") as SessionCls:
            sess = SessionCls.return_value
            resp = sess.get.return_value
            resp.raise_for_status.return_value = None
            # 第一页返回数据，第二页返回空以终止翻页
            resp.json.side_effect = [page1, []]
            codes = sm.fetch_main_board_codes(max_pages=5, use_cache=False)
        self.assertEqual(codes, ["600000", "002415"])

    def test_codes_cache_hit_skips_network(self):
        """缓存命中时不应触发网络翻页。"""
        with TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / "sina_codes.json"
            cache_file.write_text(json.dumps({
                "cached_at": (sm.datetime.now(sm.BEIJING_TZ)).isoformat(),
                "codes": ["600000", "000001"],
            }), encoding="utf-8")
            with patch.object(sm, "SINA_CODES_CACHE_FILE", cache_file), \
                 patch.object(sm, "_fetch_main_board_codes_remote") as remote:
                codes = sm.fetch_main_board_codes()
            self.assertEqual(codes, ["600000", "000001"])
            remote.assert_not_called()

    def test_codes_cache_expired_refetches_and_writes(self):
        """缓存过期时重新翻页并回写。"""
        with TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / "sina_codes.json"
            stale = datetime.now(sm.BEIJING_TZ) - timedelta(days=10)
            cache_file.write_text(json.dumps({
                "cached_at": stale.isoformat(), "codes": ["600000"],
            }), encoding="utf-8")
            with patch.object(sm, "SINA_CODES_CACHE_FILE", cache_file), \
                 patch.object(sm, "DATA_DIR", Path(tmp)), \
                 patch.object(sm, "_fetch_main_board_codes_remote",
                              return_value=["600000", "002415", "000001"]) as remote:
                codes = sm.fetch_main_board_codes()
            remote.assert_called_once()
            self.assertEqual(codes, ["600000", "002415", "000001"])
            # 回写后缓存应为新清单
            written = json.loads(cache_file.read_text(encoding="utf-8"))
            self.assertEqual(written["codes"], ["600000", "002415", "000001"])

    def test_page_timeout_retries_then_skips_without_failing_whole_list(self):
        """单页超时重试一次仍失败 → 跳过该页，其余页照常累积，不整份判空。"""
        page2 = [{"code": "000001", "symbol": "sz000001"}]
        with patch.object(sm.requests, "Session") as SessionCls, \
             patch.object(sm.time, "sleep"):
            sess = SessionCls.return_value
            good_resp = sess.get.return_value
            good_resp.raise_for_status.return_value = None
            # 第1页两次都超时(首次+重试)，第2页成功，第3页空终止
            good_resp.json.side_effect = [page2, []]
            sess.get.side_effect = [
                sm.requests.exceptions.ReadTimeout("t1"),
                sm.requests.exceptions.ReadTimeout("t1-retry"),
                good_resp, good_resp,
            ]
            codes = sm._fetch_main_board_codes_remote(max_pages=5)
        self.assertEqual(codes, ["000001"])

    def test_remote_empty_falls_back_to_stale_cache(self):
        """实时拉取返回空 → 降级用过期缓存，并记录清单日期供上层标注。"""
        with TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / "sina_codes.json"
            stale = datetime.now(sm.BEIJING_TZ) - timedelta(days=5)
            cache_file.write_text(json.dumps({
                "cached_at": stale.isoformat(), "codes": ["600000", "000001"],
            }), encoding="utf-8")
            with patch.object(sm, "SINA_CODES_CACHE_FILE", cache_file), \
                 patch.object(sm, "_fetch_main_board_codes_remote", return_value=[]):
                codes = sm.fetch_main_board_codes()
            self.assertEqual(codes, ["600000", "000001"])
            self.assertEqual(sm.get_last_codes_stale_date(), stale.strftime("%m-%d"))

    def test_fresh_fetch_clears_stale_date(self):
        """实时拉取成功时 stale 日期应为 None（不误报滞后）。"""
        with TemporaryDirectory() as tmp:
            with patch.object(sm, "SINA_CODES_CACHE_FILE", Path(tmp) / "c.json"), \
                 patch.object(sm, "DATA_DIR", Path(tmp)), \
                 patch.object(sm, "_fetch_main_board_codes_remote",
                              return_value=["600000"]):
                sm.fetch_main_board_codes()
            self.assertIsNone(sm.get_last_codes_stale_date())

    def test_stale_codes_date_propagates_to_dataframe_attrs(self):
        """清单降级日期须经 df.attrs 回传，供 multi_source 写入 source_status。"""
        rows = [{"code": "600000", "name": "浦发银行", "main_net_inflow": 1e7}]
        with patch.object(sm, "fetch_main_board_codes", return_value=["600000"]), \
             patch.object(sm, "get_last_codes_stale_date", return_value="08-10"), \
             patch.object(sm, "fetch_codes_fund_flow_sina", return_value=rows):
            df = sm.fetch_market_fund_flow_via_sina()
        self.assertEqual(df.attrs.get("codes_stale_date"), "08-10")

    def test_fetch_market_fund_flow_assembles_dataframe(self):
        rows = [
            {"code": "600000", "name": "浦发银行", "price": 9.1, "change_pct": 1.0,
             "total_amount": 5e8, "main_net_inflow": 1e7, "main_inflow_ratio": 2.0,
             "super_net": 8e6, "big_net": 2e6, "mid_net": 0, "small_net": -1e6,
             "source": "sina"},
        ]
        with patch.object(sm, "fetch_codes_fund_flow_sina", return_value=rows):
            df = sm.fetch_market_fund_flow_via_sina(codes=["600000"])
        self.assertEqual(list(df.columns), sm._COLUMNS)
        self.assertEqual(df.iloc[0]["code"], "600000")
        self.assertEqual(df.iloc[0]["source"], "sina")

    def test_empty_codes_raises(self):
        with self.assertRaises(FundFlowFetchError):
            sm.fetch_market_fund_flow_via_sina(codes=[])

    def test_all_queries_fail_raises(self):
        with patch.object(sm, "fetch_codes_fund_flow_sina", return_value=[]):
            with self.assertRaises(FundFlowFetchError):
                sm.fetch_market_fund_flow_via_sina(codes=["600000"])

    def test_verify_connectivity_pass(self):
        with patch.object(sm, "fetch_main_board_codes", return_value=["600000", "000001"]), \
             patch.object(sm, "fetch_codes_fund_flow_sina",
                          return_value=[{"code": "600000", "name": "浦发银行",
                                         "main_inflow_ratio": 2.0}]):
            r = sm.verify_sina_connectivity()
        self.assertTrue(r["node_ok"])
        self.assertTrue(r["moneyflow_ok"])
        self.assertEqual(r["sample_success"], 1)

    def test_verify_connectivity_node_fail(self):
        with patch.object(sm, "fetch_main_board_codes", return_value=[]):
            r = sm.verify_sina_connectivity()
        self.assertFalse(r["node_ok"])
        self.assertFalse(r["moneyflow_ok"])
        self.assertIsNotNone(r["error"])


if __name__ == "__main__":
    unittest.main()
