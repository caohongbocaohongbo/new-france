import unittest
from unittest.mock import patch

from backend.plugins.overnight_arbitrage.service import notify_overnight_decision


class OvernightArbitrageNotifyTest(unittest.TestCase):
    def test_notify_uses_plugin_email_sender(self):
        decision = {
            "date": "2026-06-04",
            "valid_window": "14:43-14:55",
            "status": "completed_with_errors",
            "buy_count": 1,
            "watch_count": 1,
            "total_scanned": 4121,
            "results": [
                {
                    "action": "BUY",
                    "code": "600001",
                    "name": "主板强势",
                    "decision_score": 78.5,
                    "current_price": 12.3,
                    "change_pct": 9.8,
                    "turnover": 7.2,
                    "volume_ratio": 2.8,
                    "amount": 420_000_000,
                    "intraday_pullback_pct": -2.38,
                    "reasons": ["涨幅接近涨停", "成交额充足"],
                    "risks": ["5分钟K线缺失"],
                },
                {
                    "action": "WATCH",
                    "code": "000002",
                    "name": "主板观察",
                    "decision_score": 46.2,
                    "current_price": 8.6,
                    "change_pct": 7.25,
                    "turnover": 3.8,
                    "volume_ratio": 1.7,
                    "amount": 180_000_000,
                    "intraday_pullback_pct": None,
                    "reasons": ["尾盘涨幅强"],
                    "risks": [],
                },
            ],
            "source_status": {
                "channel": {"status": "degraded", "kind": "partial_source_failure", "scope": "sources"},
                "quotes": [
                    {"source": "eastmoney_all_a", "status": "error", "count": 0, "error": "RemoteDisconnected"},
                    {"source": "sina_all_a", "status": "ok", "count": 4121},
                ],
            },
            "errors": ["eastmoney_all_a 失败: RemoteDisconnected"],
        }
        notify_config = {
            "host": "smtp.example.com",
            "port": 587,
            "user": "sender@example.com",
            "password": "secret",
            "to": "receiver@example.com",
        }

        with patch("backend.plugins.overnight_arbitrage.notifier.get_smtp_config", return_value=notify_config), \
                patch("backend.plugins.overnight_arbitrage.notifier.send_email", return_value=(True, "OK")) as send_email:
            ok = notify_overnight_decision(decision)

        self.assertTrue(ok)
        args = send_email.call_args.args
        self.assertIn("尾盘隔夜套利", args[0])
        self.assertIn("600001", args[1])
        self.assertIn("000002", args[1])
        self.assertIn("600001", args[2])
        self.assertIn("<table", args[2])
        self.assertIn("BUY 候选", args[2])
        self.assertIn("WATCH 候选", args[2])
        self.assertIn("代码", args[2])
        self.assertIn("数据源/通道", args[2])
        self.assertIn("sina_all_a", args[2])
        self.assertNotIn("<pre", args[2])
        self.assertEqual(args[3], notify_config)


if __name__ == "__main__":
    unittest.main()
