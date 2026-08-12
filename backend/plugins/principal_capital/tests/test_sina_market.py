"""sina_market 全主板数据源单元测试。"""
import unittest
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
            codes = sm.fetch_main_board_codes(max_pages=5)
        self.assertEqual(codes, ["600000", "002415"])

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
