"""eastmoney 数据源单元测试。"""
import unittest
from unittest.mock import Mock

from backend.plugins.principal_capital.sources.eastmoney import (
    FundFlowFetchError,
    fetch_market_fund_flow,
)


class EastmoneyFundFlowTest(unittest.TestCase):
    def _session_with_payloads(self, payloads):
        session = Mock()
        responses = []
        for payload in payloads:
            resp = Mock()
            if isinstance(payload, Exception):
                resp.raise_for_status.side_effect = payload
            else:
                resp.raise_for_status.return_value = None
                if payload == "__bad_json__":
                    resp.json.side_effect = ValueError("bad json")
                else:
                    resp.json.return_value = payload
            responses.append(resp)
        session.get.side_effect = responses
        return session

    def test_normal_json_maps_fields(self):
        payload = {"data": {"diff": [{
            "f12": "600001", "f14": "测试股", "f2": 10.2, "f3": 3.5,
            "f6": 1000000, "f62": 550000, "f184": 55,
            "f66": 320000, "f72": 230000, "f78": 0, "f84": -550000,
        }]}}
        empty = {"data": {"diff": []}}
        df = fetch_market_fund_flow(
            session=self._session_with_payloads([payload, empty, empty]), timeout=1)
        self.assertEqual(df.iloc[0]["code"], "600001")
        self.assertEqual(df.iloc[0]["main_inflow_ratio"], 55.0)

    def test_f184_missing_uses_fallback(self):
        payload = {"data": {"diff": [{
            "f12": "600001", "f14": "测试股", "f2": 10.2, "f3": 3.5,
            "f6": 1000000, "f62": 550000, "f184": None,
            "f66": 300000, "f72": 200000, "f78": 0, "f84": -500000,
        }]}}
        empty = {"data": {"diff": []}}
        df = fetch_market_fund_flow(
            session=self._session_with_payloads([payload, empty, empty]), timeout=1)
        self.assertAlmostEqual(df.iloc[0]["main_inflow_ratio"], 50.0)

    def test_empty_diff_stops_paging(self):
        session = self._session_with_payloads([{"data": {"diff": []}}, {"data": {"diff": []}}])
        df = fetch_market_fund_flow(session=session, timeout=1)
        self.assertTrue(df.empty)

    def test_request_exception_raises_custom_error(self):
        session = self._session_with_payloads([RuntimeError("network")])
        with self.assertRaises(FundFlowFetchError):
            fetch_market_fund_flow(session=session, timeout=1)

    def test_json_parse_failure_raises_custom_error(self):
        session = self._session_with_payloads(["__bad_json__"])
        with self.assertRaises(FundFlowFetchError):
            fetch_market_fund_flow(session=session, timeout=1)


if __name__ == "__main__":
    unittest.main()
