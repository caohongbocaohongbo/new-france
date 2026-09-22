"""23 v2 执行模式 / 唯一写者 / 副作用边界测试。"""
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from backend.plugins.principal_capital import service as pcs

BEIJING_TZ = timezone(timedelta(hours=8))


def _df(ratio=60.0, code="600001"):
    net = 2e8 * ratio / 100
    return pd.DataFrame([{"code": code, "name": "主板股", "price": 10.0, "change_pct": 2.0,
                          "total_amount": 2e8, "main_net_inflow": net, "main_inflow_ratio": ratio,
                          "super_net": net * 0.6, "big_net": net * 0.4, "mid_net": 0, "small_net": -net,
                          "source": "eastmoney"}])


def _truth(df, source="eastmoney", coverage=1.0, has_source_time=False):
    codes = list(df["code"].tolist()) if df is not None and not df.empty else []
    return (
        df,
        {"active_source": source, "is_stale": False},
        {
            "source": source, "requested_codes": codes, "received_codes": codes,
            "missing_codes": [], "coverage_ratio": coverage, "rejected_rows": {},
            "has_source_time": has_source_time,
            "valid_for_admission": bool(codes and coverage == 1.0),
        },
        "strict",
    )


def _path_patches(tmp):
    return [
        patch.object(pcs, "DATA_DIR", Path(tmp)),
        patch.object(pcs, "REPORT_DIR", Path(tmp)),
        patch.object(pcs, "REPORT_FILE", Path(tmp) / "latest.json"),
        patch.object(pcs, "HISTORY_FILE", Path(tmp) / "history.json"),
        patch.object(pcs, "SHADOW_REPORT_FILE", Path(tmp) / "shadow.json"),
        patch.object(pcs, "OWNER_CONFLICT_FILE", Path(tmp) / "owner_conflict.json"),
        patch.object(pcs, "M5_AUDIT_FILE", Path(tmp) / "m5_audit.json"),
        patch.object(pcs, "MANUAL_STATUS_FILE", Path(tmp) / "manual_status.json"),
    ]


class ExecutionModeTest(unittest.TestCase):
    def test_default_execution_mode_is_readonly(self):
        self.assertEqual(pcs.resolve_execution_mode(), "readonly")

    def test_invalid_execution_mode_raises(self):
        with self.assertRaises(ValueError):
            pcs.resolve_execution_mode("banana")

    def test_hybrid_rejected_until_m6(self):
        with self.assertRaises(RuntimeError):
            pcs.resolve_pipeline_mode("hybrid")

    def test_readonly_does_not_write_or_email(self):
        with TemporaryDirectory() as tmp:
            patches = _path_patches(tmp) + [
                patch.object(pcs, "_fetch_truth_with_fallback", return_value=_truth(_df())),
                patch.object(pcs, "send_email", return_value=(True, None)),
            ]
            for p in patches:
                p.start()
            try:
                result = pcs.run_principal_capital_scan(
                    now=datetime(2026, 9, 15, 10, 0, tzinfo=BEIJING_TZ), force=True,
                    execution_mode="readonly", enable_shadow=False)
                self.assertEqual(result["status"], "completed")
                self.assertFalse(result["email_sent"])
                self.assertFalse((Path(tmp) / "latest.json").exists())
            finally:
                for p in reversed(patches):
                    p.stop()

    def test_official_writes_report_and_emails(self):
        with TemporaryDirectory() as tmp:
            patches = _path_patches(tmp) + [
                patch.object(pcs, "_fetch_truth_with_fallback", return_value=_truth(_df(ratio=-35))),
                patch.dict(pcs.CONFIG, {"allow_provisional_notify": True}),
                patch.object(pcs, "send_email", return_value=(True, None)),
                patch.object(pcs.intraday, "acquire_owner_atomic",
                             return_value=(True, pcs.intraday.empty_state("2026-09-15"), None)),
                patch.object(pcs.intraday, "save_state", return_value=None),
            ]
            for p in patches:
                p.start()
            try:
                result = pcs.run_principal_capital_scan(
                    now=datetime(2026, 9, 15, 10, 0, tzinfo=BEIJING_TZ), force=True,
                    execution_mode="official", owner_id="github_actions", enable_shadow=False)
                self.assertEqual(result["status"], "completed")
                self.assertTrue(result["email_sent"])
                self.assertTrue((Path(tmp) / "latest.json").exists())
            finally:
                for p in reversed(patches):
                    p.stop()

    def test_owner_conflict_rejected(self):
        now = datetime(2026, 9, 15, 10, 0, tzinfo=BEIJING_TZ)
        conflicting = pcs.intraday.empty_state("2026-09-15", owner_id="other_owner",
                                               owner_lease_expires_at=(now + timedelta(minutes=5)).isoformat())
        with TemporaryDirectory() as tmp:
            patches = _path_patches(tmp) + [
                patch.object(pcs, "_fetch_truth_with_fallback", return_value=_truth(_df())),
                patch.object(pcs, "send_email", return_value=(True, None)),
                patch.object(pcs.intraday, "acquire_owner_atomic",
                             return_value=(False, conflicting, "owner_conflict: other_owner")),
                patch.object(pcs.intraday, "save_state", return_value=None),
            ]
            for p in patches:
                p.start()
            try:
                result = pcs.run_principal_capital_scan(
                    now=now, force=True, execution_mode="official", owner_id="github_actions",
                    enable_shadow=False)
            finally:
                for p in reversed(patches):
                    p.stop()
        self.assertEqual(result["status"], "owner_conflict")
        self.assertFalse(result["email_sent"])

    def test_provisional_source_blocks_direct_notify_by_default(self):
        df = _df(ratio=-35)
        with TemporaryDirectory() as tmp:
            patches = _path_patches(tmp) + [
                patch.object(pcs, "_fetch_truth_with_fallback", return_value=_truth(df, source="sina")),
                patch.object(pcs, "send_email", return_value=(True, None)),
                patch.object(pcs.intraday, "acquire_owner_atomic",
                             return_value=(True, pcs.intraday.empty_state("2026-09-15"), None)),
                patch.object(pcs.intraday, "save_state", return_value=None),
            ]
            send = patches[-2]
            for p in patches:
                p.start()
            try:
                result = pcs.run_principal_capital_scan(
                    now=datetime(2026, 9, 15, 10, 0, tzinfo=BEIJING_TZ), force=True,
                    execution_mode="official", owner_id="github_actions", enable_shadow=False)
            finally:
                for p in reversed(patches):
                    p.stop()
        self.assertEqual(result["quality"]["status"], "provisional")
        self.assertFalse(result["quality"]["notify_eligible"])
        self.assertFalse(result["email_sent"])


if __name__ == "__main__":
    unittest.main()
