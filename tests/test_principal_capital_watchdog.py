import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from scripts import principal_capital_watchdog as watchdog


BEIJING_TZ = timezone(timedelta(hours=8))


class PrincipalCapitalWatchdogTest(unittest.TestCase):

    def setUp(self):
        self.now = datetime(2026, 8, 11, 9, 45, tzinfo=BEIJING_TZ)

    def test_yesterday_snapshot_is_not_started_alert(self):
        alert = watchdog.evaluate_snapshot(
            {"status": "completed", "now": "2026-08-10T14:55:00+08:00"},
            self.now,
        )

        self.assertEqual(alert["kind"], watchdog.ALERT_NOT_STARTED)
        self.assertIn("未产生任何快照", alert["message"])

    def test_today_no_data_is_source_failure_alert(self):
        snapshot = {
            "status": "no_data",
            "now": "2026-08-11T09:40:00+08:00",
            "source_status": {
                "attempts": [{"source": "eastmoney", "status": "error", "error": "502"}],
            },
        }
        alert = watchdog.evaluate_snapshot(snapshot, self.now)

        self.assertEqual(alert["kind"], watchdog.ALERT_SOURCE_FAILURE)
        self.assertIn("数据源失败/无数据", alert["message"])
        self.assertIn("eastmoney", alert["message"])
        self.assertIn("502", alert["message"])

    def test_today_completed_snapshot_does_not_alert(self):
        alert = watchdog.evaluate_snapshot(
            {"status": "completed", "now": "2026-08-11T09:40:00+08:00"},
            self.now,
        )

        self.assertIsNone(alert)

    def test_same_alert_is_sent_once_per_day(self):
        snapshot = {"status": "no_data", "now": "2026-08-11T09:40:00+08:00"}
        sender = Mock(return_value=(True, None))
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "principal_capital_watchdog_state.json"
            first = watchdog.run_watchdog(
                now=self.now,
                snapshot_fetcher=Mock(return_value=snapshot),
                email_sender=sender,
                smtp_config_loader=Mock(return_value={}),
                state_path=state_path,
            )
            second = watchdog.run_watchdog(
                now=self.now,
                snapshot_fetcher=Mock(return_value=snapshot),
                email_sender=sender,
                smtp_config_loader=Mock(return_value={}),
                state_path=state_path,
            )

        self.assertEqual(first["status"], "alert_sent")
        self.assertEqual(second["status"], "deduplicated")
        self.assertEqual(sender.call_count, 1)
