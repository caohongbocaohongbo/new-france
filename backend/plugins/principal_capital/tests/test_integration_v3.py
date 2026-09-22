"""第三轮 P0-R/P1-R 集成测试（调用生产函数，不 mock 最终 meta）。"""
import json
import subprocess
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from backend.plugins.principal_capital import intraday_state as its
from backend.plugins.principal_capital import pipeline as pl
from backend.plugins.principal_capital import service as pcs
from backend.plugins.smart_money_radar import service as radar

BEIJING_TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 15, 14, 35, tzinfo=BEIJING_TZ)


def _truth(source="sina_full", verified=True, coverage=1.0, rejected=None):
    return {
        "source": source, "requested_codes": ["600000"], "received_codes": ["600000"],
        "missing_codes": [], "coverage_ratio": coverage, "rejected_rows": rejected or {},
        "universe_verified": verified, "has_source_time": False,
    }


def _bulk(status="shadow_only", valid=True):
    return {"status": status, "validation": {"valid": valid}}


def _audit(fn_buy=None, fn_sell=None):
    return {"false_negative_buy": fn_buy or [], "false_negative_sell": fn_sell or []}


class RoundValidTest(unittest.TestCase):
    def test_shadow_error_invalid(self):
        self.assertFalse(pl.compute_round_valid(_truth(), _bulk(status="shadow_error"), _audit(), True, True))

    def test_bulk_validation_fail_invalid(self):
        self.assertFalse(pl.compute_round_valid(_truth(), _bulk(valid=False), _audit(), True, True))

    def test_false_negative_invalid(self):
        self.assertFalse(pl.compute_round_valid(_truth(), _bulk(), _audit(fn_buy=["600000"]), True, True))

    def test_deadline_exceeded_invalid(self):
        self.assertFalse(pl.compute_round_valid(_truth(), _bulk(), _audit(), False, True))

    def test_universe_unverified_invalid(self):
        self.assertFalse(pl.compute_round_valid(_truth(verified=False), _bulk(), _audit(), True, False))

    def test_full_valid(self):
        self.assertTrue(pl.compute_round_valid(_truth(), _bulk(), _audit(), True, True))


class M5StreakTest(unittest.TestCase):
    def _records(self, day, am_valid=True, pm_valid=True):
        return [
            {"trade_date": day, "session": "am", "valid_for_admission": am_valid, "config_fingerprint": "fp"},
            {"trade_date": day, "session": "pm", "valid_for_admission": pm_valid, "config_fingerprint": "fp"},
        ]

    def test_same_day_any_failed_invalidates_day(self):
        records = self._records("2026-09-14", am_valid=False, pm_valid=True)
        records += self._records("2026-09-15", True, True)
        self.assertEqual(pl.compute_m5_streak(records, "2026-09-15"), 1)

    def test_weekend_does_not_break(self):
        # 09-11 周五有效，09-14 周一有效；中间周末不打断
        records = self._records("2026-09-11", True, True) + self._records("2026-09-14", True, True)
        self.assertEqual(pl.compute_m5_streak(records, "2026-09-14"), 2)


class ConsistencyTest(unittest.TestCase):
    def test_missing_state_consistency_error(self):
        report = {"status": "completed", "batch_id": "b1", "trade_date": "2026-09-15"}
        with patch.object(pcs.intraday, "load_state",
                          return_value=pcs.intraday.empty_state("2026-09-15")):
            out = pcs._check_report_consistency(report)
        self.assertEqual(out["status"], "degraded")
        self.assertTrue(out["consistency_error"])


class OwnerAtomicTest(unittest.TestCase):
    def test_two_writers_one_wins(self):
        results = {}
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"

            def attempt(owner):
                try:
                    ok, _state, reason = its.acquire_owner_atomic(
                        state_path, owner, 600, NOW)
                    results[owner] = (ok, reason)
                except Exception as exc:  # noqa: BLE001
                    results[owner] = (False, str(exc))

            threads = [threading.Thread(target=attempt, args=(o,)) for o in ("run_a", "run_b")]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        oks = [v[0] for v in results.values()]
        self.assertEqual(sum(oks), 1)
        self.assertEqual(len([v for v in results.values() if not v[0]]), 1)


class SentinelTest(unittest.TestCase):
    def test_sentinel_done_persisted_and_no_dup(self):
        state = its.empty_state("2026-09-15")
        label = its.should_run_sentinel(state, datetime(2026, 9, 15, 9, 36, tzinfo=BEIJING_TZ), ["09:35"])
        self.assertEqual(label, "09:35")
        state = its.mark_sentinel_done(state, label)
        self.assertIn("09:35", state["sentinel_done"])
        self.assertIsNone(its.should_run_sentinel(state, datetime(2026, 9, 15, 9, 40, tzinfo=BEIJING_TZ), ["09:35"]))


class FinalizerCrashTest(unittest.TestCase):
    def test_pending_persisted_then_restart_does_not_resend(self):
        with TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            report_file = Path(tmp) / "latest.json"
            # 子进程写入 pending+attempt_id 后直接退出，模拟进程在 SMTP 前被强杀
            code = (
                "import sys; sys.path.insert(0, %r); "
                "from backend.plugins.principal_capital import intraday_state as its; "
                "from datetime import datetime, timezone, timedelta; "
                "BEIJING_TZ = timezone(timedelta(hours=8)); "
                "now = datetime(2026, 9, 15, 14, 35, tzinfo=BEIJING_TZ); "
                "state = its.empty_state('2026-09-15'); "
                "ok, state, _ = its.acquire_owner(state, 'github_actions', 600, now); "
                "state['last_batch_id'] = 'b1'; "
                "state = its.mark_summary_pending(state, 'pm', now.isoformat(), 'attempt-1'); "
                "its.save_state(state, path=%r)"
            ) % (str(Path.cwd()), str(state_file))
            subprocess.run([sys.executable, "-c", code], check=True, cwd=Path.cwd())
            report_file.write_text(json.dumps({
                "status": "completed", "now": NOW.isoformat(), "batch_id": "b1",
                "trade_date": "2026-09-15", "quality": {"notify_eligible": True},
            }), encoding="utf-8")
            with patch.object(pcs.intraday, "INTRADAY_STATE_FILE", state_file),                  patch.object(pcs, "REPORT_FILE", report_file),                  patch.object(pcs, "send_email", return_value=(True, None)) as send:
                result = pcs.finalize_principal_capital_session(
                    "pm", now=NOW, execution_mode="official", owner_id="github_actions")
            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result["reason"], "delivery_unknown")
            send.assert_not_called()


class ScanIntegrationTest(unittest.TestCase):
    def _df(self, ratio=60.0):
        net = 2e8 * ratio / 100
        return pd.DataFrame([{"code": "600001", "name": "A", "price": 10.0, "change_pct": 1.0,
                              "total_amount": 2e8, "main_net_inflow": net, "main_inflow_ratio": ratio,
                              "super_net": net * 0.6, "big_net": net * 0.4, "mid_net": 0, "small_net": -net,
                              "source": "sina_single", "quality_status": "provisional"}])

    def _truth_tuple(self, df, source="sina_full", verified=True, coverage=1.0):
        return (df, {"active_source": source, "is_stale": False},
                {"source": source, "requested_codes": list(df.code), "received_codes": list(df.code),
                 "missing_codes": [], "coverage_ratio": coverage, "rejected_rows": {},
                 "universe_verified": verified, "has_source_time": False,
                 "valid_for_admission": False}, "strict")

    def _patch_files(self, tmp):
        return [
            patch.object(pcs, "DATA_DIR", Path(tmp)),
            patch.object(pcs, "REPORT_DIR", Path(tmp)),
            patch.object(pcs, "REPORT_FILE", Path(tmp) / "latest.json"),
            patch.object(pcs, "HISTORY_FILE", Path(tmp) / "history.json"),
            patch.object(pcs, "SHADOW_REPORT_FILE", Path(tmp) / "shadow.json"),
            patch.object(pcs, "OWNER_CONFLICT_FILE", Path(tmp) / "conflict.json"),
            patch.object(pcs, "M5_AUDIT_FILE", Path(tmp) / "m5.json"),
            patch.object(pcs, "MANUAL_STATUS_FILE", Path(tmp) / "manual.json"),
        ]

    def test_bulk_latency_over_deadline(self):
        df = self._df()
        with TemporaryDirectory() as tmp:
            saved = {}
            with patch.object(pcs, "DATA_DIR", Path(tmp)),                  patch.object(pcs, "REPORT_DIR", Path(tmp)),                  patch.object(pcs, "REPORT_FILE", Path(tmp) / "latest.json"),                  patch.object(pcs, "HISTORY_FILE", Path(tmp) / "history.json"),                  patch.object(pcs, "M5_AUDIT_FILE", Path(tmp) / "m5.json"),                  patch.dict(pcs.CONFIG, {"round_deadline_seconds": 0.05}),                  patch.object(pcs, "_fetch_truth_with_fallback",
                              return_value=self._truth_tuple(df)),                  patch.object(pcs, "_run_bulk_shadow",
                              side_effect=lambda *a, **k: (time.sleep(0.12) or {"status": "shadow_only", "validation": {"valid": True}}, _audit(), 0)),                  patch.object(pcs, "send_email", return_value=(True, None)),                  patch.object(pcs.intraday, "acquire_owner_atomic",
                              return_value=(True, pcs.intraday.empty_state("2026-09-15"), None)),                  patch.object(pcs.intraday, "save_state",
                              side_effect=lambda s: saved.update(s)):
                result = pcs.run_principal_capital_scan(
                    now=NOW, force=True, execution_mode="official", owner_id="github_actions",
                    enable_shadow=True)
        self.assertFalse(result["deadline_met"])

    def test_features_reach_pool_and_radar(self):
        df = self._df()
        with TemporaryDirectory() as tmp:
            saved = {}
            with patch.object(pcs, "DATA_DIR", Path(tmp)),                  patch.object(pcs, "REPORT_DIR", Path(tmp)),                  patch.object(pcs, "REPORT_FILE", Path(tmp) / "latest.json"),                  patch.object(pcs, "HISTORY_FILE", Path(tmp) / "history.json"),                  patch.object(pcs, "M5_AUDIT_FILE", Path(tmp) / "m5.json"),                  patch.object(pcs, "_fetch_truth_with_fallback",
                              return_value=self._truth_tuple(df)),                  patch.object(pcs, "send_email", return_value=(True, None)),                  patch.object(pcs.intraday, "acquire_owner_atomic",
                              return_value=(True, pcs.intraday.empty_state("2026-09-15"), None)),                  patch.object(pcs.intraday, "save_state",
                              side_effect=lambda s: saved.update(s)):
                pcs.run_principal_capital_scan(
                    now=NOW, force=True, execution_mode="official", owner_id="github_actions",
                    enable_shadow=False)
        pool = saved.get("pool_entries") or {}
        entry = pool.get("600001") or {}
        feat = (entry.get("latest_metrics") or {}).get("features")
        self.assertIsNotNone(feat)
        for key in ("roll_net_30m", "acc_win", "inc_ratio_5m", "interval_seconds", "warming", "warming_reason"):
            self.assertIn(key, feat)


if __name__ == "__main__":
    unittest.main()
