import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import get_app


class NationalTeamApiTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(get_app())

    def test_summary_endpoint_returns_service_payload(self):
        payload = {
            "total_holdings": 1,
            "entities": [],
            "source_status": {"ok": True, "source": "东方财富股东分析"},
        }
        with patch("backend.api.router_national_team.get_summary", return_value=payload):
            resp = self.client.get("/api/v1/national-team/summary")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["total_holdings"], 1)

    def test_holdings_endpoint_passes_filters(self):
        payload = {"total": 0, "page": 1, "size": 20, "items": []}
        with patch("backend.api.router_national_team.list_holdings", return_value=payload) as func:
            resp = self.client.get("/api/v1/national-team/holdings?entity=csf&page=1&size=20")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["items"], [])
        self.assertEqual(func.call_args.kwargs["entity"], "csf")

    def test_refresh_endpoint_returns_refresh_result(self):
        payload = {"ok": True, "holding_count": 2, "periods": ["2026-03-31"]}
        with patch("backend.api.router_national_team.refresh_national_team_data", return_value=payload):
            resp = self.client.post("/api/v1/national-team/refresh")

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])

    def test_refresh_endpoint_returns_503_when_source_is_partial_or_failed(self):
        payload = {
            "ok": False,
            "holding_count": 2,
            "source_status": {"ok": False, "errors": ["部分过滤条件失败"]},
        }
        with patch("backend.api.router_national_team.refresh_national_team_data", return_value=payload):
            resp = self.client.post("/api/v1/national-team/refresh")

        self.assertEqual(resp.status_code, 503)
        self.assertFalse(resp.json()["detail"]["source_status"]["ok"])

    def test_events_endpoint_passes_offset_for_scroll_loading(self):
        payload = {"total": 12, "items": [], "source_status": {"ok": True}}
        with patch("backend.api.router_national_team.list_events", return_value=payload) as func:
            resp = self.client.get("/api/v1/national-team/events?entity=huijin&limit=5&offset=5")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(func.call_args.kwargs["entity"], "huijin")
        self.assertEqual(func.call_args.kwargs["limit"], 5)
        self.assertEqual(func.call_args.kwargs["offset"], 5)

    def test_top_holders_endpoint_returns_real_stats_payload(self):
        payload = {
            "limit": 10,
            "entities": [
                {"entity_key": "huijin", "entity_name": "中央汇金", "items": []},
                {"entity_key": "social_security", "entity_name": "社保基金", "items": []},
                {"entity_key": "csf", "entity_name": "证金公司", "items": []},
            ],
            "source_status": {"ok": True, "source": "东方财富股东分析"},
        }
        with patch("backend.api.router_national_team.get_top_holder_stats", return_value=payload) as func:
            resp = self.client.get("/api/v1/national-team/top-holders?limit=10")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["limit"], 10)
        self.assertEqual(len(resp.json()["entities"]), 3)
        self.assertEqual(func.call_args.kwargs["limit"], 10)

    def test_capital_flows_endpoint_returns_buy_sell_payload(self):
        payload = {
            "limit": 10,
            "entities": [
                {
                    "entity_key": "huijin",
                    "entity_name": "中央汇金",
                    "buy": {"items": [{"stock_code": "600001", "change_amount": 40.0}]},
                    "sell": {"items": [{"stock_code": "600002", "change_amount": 20.0}]},
                },
                {"entity_key": "social_security", "entity_name": "社保基金", "buy": {"items": []}, "sell": {"items": []}},
                {"entity_key": "csf", "entity_name": "证金公司", "buy": {"items": []}, "sell": {"items": []}},
            ],
            "source_status": {"ok": True, "source": "东方财富股东分析"},
        }
        with patch("backend.api.router_national_team.get_capital_flow_stats", return_value=payload) as func:
            resp = self.client.get("/api/v1/national-team/capital-flows?limit=10")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["limit"], 10)
        self.assertIn("buy", resp.json()["entities"][0])
        self.assertIn("sell", resp.json()["entities"][0])
        self.assertEqual(func.call_args.kwargs["limit"], 10)

    def test_holding_values_endpoint_returns_full_names_and_items(self):
        payload = {
            "limit": 10,
            "entities": [
                {
                    "entity_key": "huijin",
                    "entity_name": "中央汇金",
                    "full_name": "中央汇金投资有限责任公司",
                    "english_name": "Central Huijin Investment Ltd.",
                    "items": [{"stock_code": "601939", "stock_name": "建设银行", "market_value": 1.0}],
                },
                {"entity_key": "social_security", "entity_name": "社保基金", "full_name": "全国社会保障基金理事会", "english_name": "National Council for Social Security Fund", "items": []},
                {"entity_key": "csf", "entity_name": "证金公司", "full_name": "中国证券金融股份有限公司", "english_name": "", "items": []},
            ],
            "source_status": {"ok": True, "source": "东方财富股东分析"},
        }
        with patch("backend.api.router_national_team.get_holding_value_stats", return_value=payload) as func:
            resp = self.client.get("/api/v1/national-team/holding-values?limit=10")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["entities"][0]["full_name"], "中央汇金投资有限责任公司")
        self.assertEqual(resp.json()["entities"][0]["items"][0]["stock_name"], "建设银行")
        self.assertEqual(func.call_args.kwargs["limit"], 10)


if __name__ == "__main__":
    unittest.main()
