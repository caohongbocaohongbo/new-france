"""sina 单股数据源单元测试。"""
import unittest
from unittest.mock import Mock, patch

from backend.plugins.principal_capital.sources.sina import (
    fetch_codes_fund_flow_sina,
    fetch_single_stock_fund_flow_sina,
)


class SinaFundFlowTest(unittest.TestCase):
    def test_single_stock_parses_response(self):
        session = Mock()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [{
            "name": "测试股", "trade": "10.5",
            "r0_in": "200", "r0_out": "50",
            "r1_in": "100", "r1_out": "20",
            "r2_in": "60", "r2_out": "40",
            "r3_in": "30", "r3_out": "20",
        }]
        session.get.return_value = response
        result = fetch_single_stock_fund_flow_sina("600001", session=session)
        self.assertEqual(result["code"], "600001")
        self.assertAlmostEqual(result["main_net_inflow"], 230.0)

    def test_single_stock_parses_dict_response(self):
        """新浪单股查询返回 dict（非 list）——历史 bug：旧代码只认 list 恒返回 None。"""
        session = Mock()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "name": "测试股", "trade": "10.5", "changeratio": "0.05",
            "r0_in": "200", "r0_out": "50",
            "r1_in": "100", "r1_out": "20",
            "r2_in": "60", "r2_out": "40",
            "r3_in": "30", "r3_out": "20",
        }
        session.get.return_value = response
        result = fetch_single_stock_fund_flow_sina("600001", session=session)
        self.assertIsNotNone(result)
        self.assertEqual(result["code"], "600001")
        # main_net = (r0_in+r1_in) - (r0_out+r1_out) = 300 - 70 = 230
        self.assertAlmostEqual(result["main_net_inflow"], 230.0)
        # 补齐字段应对齐东财列
        self.assertAlmostEqual(result["super_net"], 150.0)   # r0_in - r0_out
        self.assertAlmostEqual(result["big_net"], 80.0)      # r1_in - r1_out
        self.assertAlmostEqual(result["total_amount"], 520.0)
        self.assertAlmostEqual(result["change_pct"], 5.0)    # changeratio*100
        for col in ("mid_net", "small_net", "main_inflow_ratio"):
            self.assertIn(col, result)

    def test_network_error_returns_none(self):
        session = Mock()
        session.get.side_effect = RuntimeError("network")
        self.assertIsNone(fetch_single_stock_fund_flow_sina("600001", session=session))

    def test_batch_query_skips_failed_rows(self):
        def fake_fetch(code, timeout=8):
            del timeout
            if code in {"600002", "600004"}:
                return None
            return {"code": code, "name": f"股票{code}", "price": 10,
                    "main_net_inflow": 1, "main_inflow_ratio": 1, "source": "sina"}

        with patch(
            "backend.plugins.principal_capital.sources.sina.fetch_single_stock_fund_flow_sina",
            side_effect=fake_fetch,
        ):
            rows = fetch_codes_fund_flow_sina(
                ["600001", "600002", "600003", "600004", "600005"], max_workers=3)
        self.assertEqual(len(rows), 3)


if __name__ == "__main__":
    unittest.main()
