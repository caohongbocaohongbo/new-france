"""23 v2 午间/收盘 finalizer 测试。"""
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.plugins.principal_capital import service as pcs
from backend.plugins.principal_capital import intraday_state as its

BEIJING_TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 15, 11, 30, tzinfo=BEIJING_TZ)
OWNER = "github_actions"


def _write_state(state_file, owner=OWNER, batch_id="b1"):
    state = its.empty_state("2026-09-15")
    ok, state, _ = its.acquire_owner(state, owner, 600, NOW)
    assert ok
    state["last_batch_id"] = batch_id
    state_file.parent.mkdir(parents=True, exist_ok=True)
    its.save_state(state, path=state_file)


def _write_report(report_file, now_iso, batch_id="b1", notify_eligible=True, status="completed"):
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps({
        "status": status, "now": now_iso, "batch_id": batch_id, "trade_date": "2026-09-15",
        "quality": {"notify_eligible": notify_eligible},
    }), encoding="utf-8")


class SessionFinalizerTest(unittest.TestCase):
    def _patches(self, tmp):
        state_file = Path(tmp) / "intraday_state.json"
        report_file = Path(tmp) / "latest.json"
        return (
            patch.object(pcs.intraday, "INTRADAY_STATE_FILE", state_file),
            patch.object(pcs, "REPORT_FILE", report_file),
        ), state_file, report_file

    def test_official_sends_once(self):
        with TemporaryDirectory() as tmp:
            patches, state_file, report_file = self._patches(tmp)
            _write_state(state_file)
            _write_report(report_file, NOW.isoformat())
            for p in patches:
                p.start()
            try:
                with patch.object(pcs, "send_email", return_value=(True, None)):
                    first = pcs.finalize_principal_capital_session(
                        "am", now=NOW, execution_mode="official", owner_id=OWNER)
            finally:
                for p in patches:
                    p.stop()
        self.assertEqual(first["status"], "sent")
        self.assertTrue(first["email_sent"])

    def test_owner_mismatch_conflict(self):
        with TemporaryDirectory() as tmp:
            patches, state_file, report_file = self._patches(tmp)
            _write_state(state_file, owner="other_owner")
            _write_report(report_file, NOW.isoformat())
            for p in patches:
                p.start()
            try:
                with patch.object(pcs, "send_email", return_value=(True, None)) as send:
                    result = pcs.finalize_principal_capital_session(
                        "am", now=NOW, execution_mode="official", owner_id=OWNER)
            finally:
                for p in patches:
                    p.stop()
        self.assertEqual(result["status"], "owner_conflict")
        send.assert_not_called()

    def test_delivery_unknown_blocks_resend(self):
        with TemporaryDirectory() as tmp:
            patches, state_file, report_file = self._patches(tmp)
            _write_state(state_file)
            _write_report(report_file, NOW.isoformat())
            for p in patches:
                p.start()
            try:
                with patch.object(pcs, "send_email", side_effect=RuntimeError("smtp boom")):
                    first = pcs.finalize_principal_capital_session(
                        "am", now=NOW, execution_mode="official", owner_id=OWNER)
                # 重新续租（模拟下一次调度），再次调用不得自动重发
                state = its.load_state(path=state_file, now=NOW)
                ok, state, _ = its.acquire_owner(state, OWNER, 600, NOW)
                its.save_state(state, path=state_file)
                with patch.object(pcs, "send_email", return_value=(True, None)) as send:
                    second = pcs.finalize_principal_capital_session(
                        "am", now=NOW, execution_mode="official", owner_id=OWNER)
            finally:
                for p in patches:
                    p.stop()
        self.assertEqual(first["status"], "delivery_unknown")
        self.assertEqual(second["status"], "skipped")
        self.assertEqual(second["reason"], "delivery_unknown")
        send.assert_not_called()

    def test_provisional_blocks(self):
        with TemporaryDirectory() as tmp:
            patches, state_file, report_file = self._patches(tmp)
            _write_state(state_file)
            _write_report(report_file, NOW.isoformat(), notify_eligible=False)
            for p in patches:
                p.start()
            try:
                with patch.object(pcs, "send_email", return_value=(True, None)) as send:
                    result = pcs.finalize_principal_capital_session(
                        "am", now=NOW, execution_mode="official", owner_id=OWNER)
            finally:
                for p in patches:
                    p.stop()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "notify_not_eligible")
        send.assert_not_called()

    def test_no_complete_batch_skipped(self):
        with TemporaryDirectory() as tmp:
            patches, state_file, report_file = self._patches(tmp)
            _write_state(state_file)
            _write_report(report_file, NOW.isoformat(), status="no_data")
            for p in patches:
                p.start()
            try:
                result = pcs.finalize_principal_capital_session(
                    "am", now=NOW, execution_mode="official", owner_id=OWNER)
            finally:
                for p in patches:
                    p.stop()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no_complete_batch")

    def test_shadow_constructs_without_sending(self):
        with TemporaryDirectory() as tmp:
            patches, state_file, report_file = self._patches(tmp)
            _write_state(state_file)
            _write_report(report_file, NOW.isoformat())
            for p in patches:
                p.start()
            try:
                with patch.object(pcs, "send_email", return_value=(True, None)) as send:
                    result = pcs.finalize_principal_capital_session(
                        "am", now=NOW, execution_mode="shadow")
            finally:
                for p in patches:
                    p.stop()
        self.assertEqual(result["status"], "constructed")
        self.assertIn("subject", result)
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
