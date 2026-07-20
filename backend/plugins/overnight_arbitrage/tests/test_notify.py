import unittest
from unittest.mock import Mock, patch

from backend.plugins.overnight_arbitrage import notifier
from backend.plugins.overnight_arbitrage.service import notify_overnight_decision


class OvernightArbitrageNotifyTest(unittest.TestCase):
    def test_send_email_prefers_brevo_https_api(self):
        response = Mock(status_code=201, text='{"messageId":"test-id"}')
        config = {
            "host": "smtp.example.com",
            "port": 587,
            "user": "sender@example.com",
            "password": "secret",
            "to": " receiver@example.com, second@example.com, , ",
            "brevo_api_key": "brevo-secret",
            "idempotency_key": "overnight-arbitrage-2026-06-04",
        }

        with patch("backend.plugins.overnight_arbitrage.notifier.requests.post", return_value=response) as post, \
                patch("backend.plugins.overnight_arbitrage.notifier.smtplib.SMTP") as smtp, \
                patch("backend.plugins.overnight_arbitrage.notifier.smtplib.SMTP_SSL") as smtp_ssl:
            ok, error = notifier.send_email("测试主题", "纯文本", "<p>HTML</p>", config)

        self.assertTrue(ok)
        self.assertIsNone(error)
        post.assert_called_once()
        smtp.assert_not_called()
        smtp_ssl.assert_not_called()
        self.assertEqual(post.call_args.args[0], "https://api.brevo.com/v3/smtp/email")
        self.assertEqual(post.call_args.kwargs["headers"]["api-key"], "brevo-secret")
        self.assertEqual(post.call_args.kwargs["timeout"], 15)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["sender"]["email"], "sender@example.com")
        self.assertEqual(payload["to"], [
            {"email": "receiver@example.com"},
            {"email": "second@example.com"},
        ])
        self.assertEqual(payload["subject"], "测试主题")
        self.assertEqual(payload["headers"]["Idempotency-Key"], "overnight-arbitrage-2026-06-04")

    def test_send_email_does_not_fallback_to_smtp_when_brevo_rejects_request(self):
        response = Mock(status_code=401, text="unauthorized")
        config = {
            "host": "smtp.example.com",
            "port": 587,
            "user": "sender@example.com",
            "password": "secret",
            "to": "receiver@example.com",
            "brevo_api_key": "invalid-key",
        }

        with patch("backend.plugins.overnight_arbitrage.notifier.requests.post", return_value=response), \
                patch("backend.plugins.overnight_arbitrage.notifier.smtplib.SMTP") as smtp:
            ok, error = notifier.send_email("测试主题", "纯文本", "<p>HTML</p>", config)

        self.assertFalse(ok)
        self.assertIn("Brevo API 返回 401", error)
        smtp.assert_not_called()

    def test_send_via_brevo_rejects_empty_api_key_without_http_request(self):
        config = {
            "user": "sender@example.com",
            "to": "receiver@example.com",
            "brevo_api_key": "  ",
        }

        with patch("backend.plugins.overnight_arbitrage.notifier.requests.post") as post:
            ok, error = notifier._send_via_brevo("测试主题", "纯文本", "<p>HTML</p>", config, 15)

        self.assertFalse(ok)
        self.assertIn("BREVO_API_KEY", error)
        post.assert_not_called()

    def test_notify_uses_plugin_email_sender(self):
        decision = {
            "date": "2026-06-04",
            "generated_at": "2026-06-04 14:45:10",
            "valid_window": "14:40-14:55",
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
            "data_quality": {
                "status": "complete",
                "removed_count": 1,
                "removed": [{
                    "code": "600099",
                    "name": "字段不全",
                    "missing_fields": ["volume_ratio"],
                }],
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
        self.assertIn("14:45", args[0])
        self.assertIn("Top20", args[0])
        self.assertIn("600001", args[1])
        self.assertIn("000002", args[1])
        self.assertIn("14:40-14:55", args[1])
        self.assertIn("已剔除 1 只行情字段不全个股", args[1])
        self.assertIn("600001", args[2])
        self.assertIn("14:40-14:55", args[2])
        self.assertIn("已剔除 1 只行情字段不全个股", args[2])
        self.assertIn("<table", args[2])
        self.assertIn("BUY 候选", args[2])
        self.assertIn("WATCH 候选", args[2])
        self.assertIn("代码", args[2])
        self.assertIn("数据源/通道", args[2])
        self.assertIn("sina_all_a", args[2])
        self.assertNotIn("<pre", args[2])
        self.assertEqual({key: args[3][key] for key in notify_config}, notify_config)
        self.assertEqual(args[3]["idempotency_key"], "overnight-arbitrage-2026-06-04")


if __name__ == "__main__":
    unittest.main()
