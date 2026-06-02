import unittest
from unittest.mock import patch

from backend.agents.layer1_data_collector.sources import national_team


class NationalTeamSourceTest(unittest.TestCase):
    def test_match_entity_from_holder_name(self):
        self.assertEqual(
            national_team.match_entity("中央汇金资产管理有限责任公司"),
            ("huijin", "中央汇金"),
        )
        self.assertEqual(
            national_team.match_entity("中国证券金融股份有限公司"),
            ("csf", "证金公司"),
        )
        self.assertEqual(
            national_team.match_entity("全国社保基金一一四组合"),
            ("social_security", "社保基金"),
        )
        self.assertIsNone(national_team.match_entity("香港中央结算有限公司"))

    def test_normalize_free_float_row_keeps_source_fields(self):
        row = {
            "HOLDER_NAME": "中国证券金融股份有限公司",
            "HOLDER_TYPE": "证券公司",
            "SHARES_TYPE": "流通A股",
            "HOLDER_RANK": 3,
            "SECURITY_CODE": "000002",
            "SECURITY_NAME_ABBR": "万科A",
            "HOLD_NUM": 132669394,
            "FREE_HOLDNUM_RATIO": 1.36,
            "XZCHANGE": 0,
            "CHANGE_RATIO": 0,
            "HOLDNUM_CHANGE_NAME": "不变",
            "HOLDER_MARKET_CAP": 900000000,
            "END_DATE": "2025-09-30 00:00:00",
            "UPDATE_DATE": "2025-10-31 00:00:00",
        }

        item = national_team.normalize_holder_row(
            row,
            holding_type="free_float_top10",
            source_url="https://data.eastmoney.com/gdfx/HoldingAnalyse.html",
        )

        self.assertEqual(item["entity_key"], "csf")
        self.assertEqual(item["entity_name"], "证金公司")
        self.assertEqual(item["stock_code"], "000002")
        self.assertEqual(item["report_period"], "2025-09-30")
        self.assertEqual(item["notice_date"], "2025-10-31")
        self.assertEqual(item["holding_type"], "free_float_top10")
        self.assertEqual(item["holding_type_name"], "十大流通股东")
        self.assertEqual(item["shares"], 132669394.0)
        self.assertEqual(item["shares_change"], 0.0)
        self.assertEqual(item["change_ratio"], 0.0)
        self.assertEqual(item["source"], "东方财富股东分析")
        self.assertIn("source_url", item)

    def test_free_float_row_uses_hold_num_change_not_xzchange(self):
        row = {
            "HOLDER_NAME": "中国证券金融股份有限公司",
            "HOLDER_TYPE": "证券公司",
            "SHARES_TYPE": "流通A股",
            "HOLDER_RANK": 3,
            "SECURITY_CODE": "000002",
            "SECURITY_NAME_ABBR": "万科A",
            "HOLD_NUM": 132669394,
            "FREE_HOLDNUM_RATIO": 1.36,
            "HOLD_NUM_CHANGE": -1200,
            "XZCHANGE": 0,
            "HOLDNUM_CHANGE_RATIO": -0.03,
            "CHANGE_RATIO": 0,
            "HOLDNUM_CHANGE_NAME": "减少",
            "HOLDER_MARKET_CAP": 900000000,
            "END_DATE": "2025-09-30 00:00:00",
            "UPDATE_DATE": "2025-10-31 00:00:00",
        }

        item = national_team.normalize_holder_row(row, holding_type="free_float_top10")

        self.assertEqual(item["shares_change"], -1200.0)
        self.assertEqual(item["change_ratio"], -0.03)

    def test_fetch_latest_periods_uses_small_page_probe(self):
        payload = {
            "success": True,
            "result": {
                "data": [
                    {"END_DATE": "2026-05-25 00:00:00"},
                    {"END_DATE": "2026-03-31 00:00:00"},
                    {"END_DATE": "2025-12-31 00:00:00"},
                ]
            },
        }

        with patch.object(national_team, "_eastmoney_get", return_value=payload) as get:
            periods = national_team.fetch_latest_periods(limit=2)

        self.assertEqual(periods, ["2026-05-25", "2026-03-31"])
        self.assertEqual(get.call_args.kwargs["page_size"], 20)

    def test_fetch_holdings_without_period_queries_entity_filters_directly(self):
        sample_rows = [
            {
                "HOLDER_NAME": "中国证券金融股份有限公司",
                "HOLDER_TYPE": "证券公司",
                "SECURITY_CODE": "000858",
                "SECURITY_NAME_ABBR": "五粮液",
                "END_DATE": "2026-05-11 00:00:00",
                "UPDATE_DATE": "2026-05-16 00:00:00",
                "HOLD_NUM": 76044681,
                "HOLDNUM_CHANGE_NAME": "减少",
                "HOLDER_RANK": 3,
            }
        ]

        def fake_get(report_name, filter_expr=None, **kwargs):
            if 'HOLDER_NAME="中国证券金融股份有限公司"' in str(filter_expr):
                return {"success": True, "result": {"data": sample_rows, "pages": 1, "count": 1}}
            return {"success": False, "result": None, "message": "返回数据为空"}

        with patch.object(national_team, "_eastmoney_get", side_effect=fake_get) as get:
            holdings, meta = national_team.fetch_holdings(periods=None, max_pages_per_filter=1, page_size=5)

        self.assertTrue(any(item["entity_key"] == "csf" for item in holdings))
        self.assertEqual(meta["periods"], ["latest"])
        self.assertTrue(all("END_DATE=" not in str(call.kwargs.get("filter_expr")) for call in get.call_args_list))


if __name__ == "__main__":
    unittest.main()
