import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


class OptionalSourceHealthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_path = Path(self.tmp.name) / "source_health.json"

    def test_successes_accumulate_until_source_is_stable(self):
        from backend.services.optional_source_health import OptionalSourceHealthStore

        now = datetime.now(timezone(timedelta(hours=8)))
        store = OptionalSourceHealthStore(self.state_path, required_successes=2, max_age_hours=24)
        first = store.record_result(
            "national_team",
            {
                "ok": True,
                "source_status": {
                    "source": "东方财富股东分析",
                    "source_url": "https://data.eastmoney.com/gdfx/HoldingAnalyse.html",
                    "fetched_at": (now - timedelta(minutes=5)).isoformat(),
                },
                "holding_count": 12,
            },
        )
        second = store.record_result(
            "national_team",
            {
                "ok": True,
                "source_status": {
                    "source": "东方财富股东分析",
                    "source_url": "https://data.eastmoney.com/gdfx/HoldingAnalyse.html",
                    "fetched_at": now.isoformat(),
                },
                "holding_count": 16,
            },
        )

        self.assertFalse(first["stable"])
        self.assertTrue(second["stable"])
        self.assertEqual(second["consecutive_successes"], 2)
        self.assertEqual(second["data"]["record_count"], 16)
        self.assertTrue(store.is_promoted("national_team"))

    def test_failure_resets_consecutive_successes_and_keeps_error(self):
        from backend.services.optional_source_health import OptionalSourceHealthStore

        store = OptionalSourceHealthStore(self.state_path, required_successes=2, max_age_hours=24)
        store.record_result("national_team", {"ok": True, "holding_count": 12})
        failed = store.record_result(
            "national_team",
            {"ok": False, "source_status": {"errors": ["东方财富接口 502"], "fetched_at": datetime.now(timezone(timedelta(hours=8))).isoformat()}},
        )

        self.assertFalse(failed["ok"])
        self.assertFalse(failed["stable"])
        self.assertEqual(failed["consecutive_successes"], 0)
        self.assertEqual(failed["error"], "东方财富接口 502")
        self.assertFalse(store.is_promoted("national_team"))

    def test_explicit_env_switch_can_enable_or_disable_promotion(self):
        from backend.services.optional_source_health import OptionalSourceHealthStore

        stale_time = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=3)).isoformat()
        store = OptionalSourceHealthStore(self.state_path, required_successes=5, max_age_hours=1)
        store.record_result(
            "national_team",
            {"ok": True, "holding_count": 12, "source_status": {"fetched_at": stale_time}},
        )

        self.assertFalse(store.is_promoted("national_team"))
        with patch.dict(os.environ, {"OPTIONAL_SOURCE_NATIONAL_TEAM_PROMOTED": "1"}):
            self.assertTrue(store.is_promoted("national_team"))
        with patch.dict(os.environ, {"OPTIONAL_SOURCE_NATIONAL_TEAM_PROMOTED": "0"}):
            self.assertFalse(store.is_promoted("national_team"))


if __name__ == "__main__":
    unittest.main()
