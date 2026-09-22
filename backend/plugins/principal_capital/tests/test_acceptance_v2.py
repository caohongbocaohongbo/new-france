"""23 v2 修复清单 §7 验收用例 A01–A16（离线，patch 外部源）。"""
import asyncio
import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from backend.plugins.principal_capital import pipeline as pl
from backend.plugins.principal_capital import service as pcs
from backend.plugins.principal_capital.sources.sina import _parse_sina_flow_item, _run_fetch_batch
from backend.plugins.smart_money_radar import service as radar

BEIJING_TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 15, 14, 35, tzinfo=BEIJING_TZ)


def _truth(source="sina_full", requested=3200, received=1, coverage=None, valid=True):
    codes = [f"{100000 + i:06d}" for i in range(requested)]
    recv = codes[:received]
    coverage = (received / requested) if coverage is None else coverage
    df = pd.DataFrame([{"code": recv[0], "name": "A", "price": 10.0, "change_pct": 1.0,
                        "total_amount": 2e8, "main_net_inflow": 6e7, "main_inflow_ratio": 30.0,
                        "super_net": 3e7, "big_net": 3e7, "mid_net": 0, "small_net": -6e7,
                        "source": "sina_single"}] if recv else [])
    return (
        df,
        {"active_source": source, "is_stale": False},
        {
            "source": source, "requested_codes": codes, "received_codes": recv,
            "missing_codes": sorted(set(codes) - set(recv)), "coverage_ratio": coverage,
            "rejected_rows": {}, "has_source_time": False, "valid_for_admission": valid,
        },
        "strict",
    )


def _df(ratio=-35.0, code="600001"):
    net = 2e8 * ratio / 100
    return pd.DataFrame([{"code": code, "name": "主板股", "price": 10.0, "change_pct": 2.0,
                          "total_amount": 2e8, "main_net_inflow": net, "main_inflow_ratio": ratio,
                          "super_net": net * 0.6, "big_net": net * 0.4, "mid_net": 0, "small_net": -net,
                          "source": "eastmoney"}])


class AcceptanceTest(unittest.TestCase):
    def test_a01_partial_coverage_not_completed(self):
        # A01：universe 3200、只返回 1 行 -> partial，coverage=1/3200，missing=3199
        with TemporaryDirectory() as tmp:
            with patch.object(pcs, "DATA_DIR", Path(tmp)), \
                 patch.object(pcs, "REPORT_DIR", Path(tmp)), \
                 patch.object(pcs, "REPORT_FILE", Path(tmp) / "latest.json"), \
                 patch.object(pcs, "HISTORY_FILE", Path(tmp) / "history.json"), \
                 patch.object(pcs, "SHADOW_REPORT_FILE", Path(tmp) / "shadow.json"), \
                 patch.object(pcs, "_fetch_truth_with_fallback",
                              return_value=_truth(requested=3200, received=1)):
                result = pcs.run_principal_capital_scan(
                    now=NOW, force=True, execution_mode="readonly", enable_shadow=False)
        self.assertIn(result["status"], {"partial", "degraded"})
        self.assertEqual(result["refine"]["requested_count"], 3200)
        self.assertEqual(result["refine"]["received_count"], 1)
        self.assertEqual(result["refine"]["missing_count"], 3199)
        self.assertAlmostEqual(result["refine"]["coverage_ratio"], 1 / 3200)

    def test_a02_truth_not_sina_full_not_admission(self):
        # A02：truth 非 sina_full -> audit.valid_for_admission=false
        with TemporaryDirectory() as tmp:
            with patch.object(pcs, "DATA_DIR", Path(tmp)), \
                 patch.object(pcs, "REPORT_DIR", Path(tmp)), \
                 patch.object(pcs, "REPORT_FILE", Path(tmp) / "latest.json"), \
                 patch.object(pcs, "HISTORY_FILE", Path(tmp) / "history.json"), \
                 patch.object(pcs, "M5_AUDIT_FILE", Path(tmp) / "m5.json"), \
                 patch.object(pcs, "SHADOW_REPORT_FILE", Path(tmp) / "shadow.json"), \
                 patch.object(pcs, "_fetch_truth_with_fallback",
                              return_value=_truth(source="eastmoney", valid=False)), \
                 patch.object(pcs, "_run_bulk_shadow",
                              return_value=({"status": "shadow_only"}, {"kind": "shadow_truth"}, 0)), \
                 patch.object(pcs, "send_email", return_value=(True, None)), \
                 patch.object(pcs.intraday, "acquire_owner_atomic",
                              return_value=(True, pcs.intraday.empty_state("2026-09-15"), None)), \
                 patch.object(pcs.intraday, "save_state", return_value=None):
                result = pcs.run_principal_capital_scan(
                    now=NOW, force=True, execution_mode="official", owner_id="github_actions",
                    enable_shadow=True)
        self.assertFalse(result["audit"]["valid_for_admission"])

    def test_a09_api_trigger_does_not_write_official(self):
        # A09：API /trigger 前后 official latest 字节不变
        from backend.plugins.principal_capital import router

        async def _call():
            from starlette.background import BackgroundTasks
            bg = BackgroundTasks()
            return await router.trigger_principal_capital(
                background_tasks=bg, buy_threshold=50, sell_threshold=30,
                exclude_star=True, dry_run=False, force=True, enable_verify=False,
                execution_mode="shadow",
            )

        with TemporaryDirectory() as tmp:
            report_file = Path(tmp) / "latest.json"
            report_file.write_text('{"status":"completed","batch_id":"keep"}', encoding="utf-8")
            before = report_file.read_bytes()
            with patch.object(router, "write_manual_status") as wms, \
                 patch.object(pcs, "REPORT_FILE", report_file):
                result = asyncio.run(_call())
            after = report_file.read_bytes()
        self.assertEqual(result["status"], "started")
        self.assertEqual(before, after)
        wms.assert_called_once()

    def test_a10_sina_required_field_invalid_rejected(self):
        # A10：r0/r1 必需字段缺失 -> 行被拒绝并记录原因
        row, reason = _parse_sina_flow_item({"r0_in": "100", "r0_out": None}, "600001", NOW)
        self.assertIsNone(row)
        self.assertIn("missing_required_field", reason)

    def test_a11_batch_timeout_is_real_wall_clock(self):
        # A11：250ms 任务、batch timeout 30ms -> 不等待全部完成
        def slow(code, single_timeout, fetched_at):
            time.sleep(0.25)
            return ({"code": code}, None)

        t0 = time.monotonic()
        _rows, rejected = _run_fetch_batch(
            ["600001", "600002", "600003", "600004"], 2, 8, 0.03, NOW, slow)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 0.2)
        self.assertGreaterEqual(len(rejected), 1)

    def test_a13_authoritative_empty_current(self):
        # A13：buy_candidates_current=[] 且 legacy 有旧数据 -> 权威空，不回退
        items = radar._candidate_lists({
            "buy_candidates_current": [],
            "buy_triggered": [{"code": "600001"}],
        })
        self.assertEqual(items, [])

    def test_a14_state_report_batch_mismatch(self):
        # A14：state/report batch_id 不一致 -> degraded + consistency_error
        report = {"status": "completed", "batch_id": "b2", "trade_date": "2026-09-15"}
        with patch.object(pcs.intraday, "load_state", return_value={"last_batch_id": "b1"}):
            out = pcs._check_report_consistency(report)
        self.assertEqual(out["status"], "degraded")
        self.assertTrue(out["consistency_error"])

    def test_a15_conflict_does_not_write_official(self):
        # A15：不同 owner 并发 -> 拒绝方不改 official 文件
        now = datetime(2026, 9, 15, 10, 0, tzinfo=BEIJING_TZ)
        conflicting = pcs.intraday.empty_state(
            "2026-09-15", owner_id="run_a",
            owner_lease_expires_at=(now + timedelta(minutes=5)).isoformat())
        with TemporaryDirectory() as tmp:
            report_file = Path(tmp) / "latest.json"
            report_file.write_text('{"status":"completed","batch_id":"keep"}', encoding="utf-8")
            before = report_file.read_bytes()
            with patch.object(pcs, "DATA_DIR", Path(tmp)), \
                 patch.object(pcs, "REPORT_DIR", Path(tmp)), \
                 patch.object(pcs, "REPORT_FILE", report_file), \
                 patch.object(pcs, "HISTORY_FILE", Path(tmp) / "history.json"), \
                 patch.object(pcs, "OWNER_CONFLICT_FILE", Path(tmp) / "conflict.json"), \
                 patch.object(pcs.intraday, "load_state", return_value=conflicting), \
                 patch.object(pcs.intraday, "save_state", return_value=None):
                result = pcs.run_principal_capital_scan(
                    now=now, force=True, execution_mode="official", owner_id="run_b",
                    enable_shadow=False)
            after = report_file.read_bytes()
        self.assertEqual(result["status"], "owner_conflict")
        self.assertEqual(before, after)

    def _day_records(self, day, valid=True):
        # 每个有效交易日至少 am + pm 两轮，且均 valid、指纹一致
        return [
            {"trade_date": day, "session": "am", "valid_for_admission": valid, "config_fingerprint": "fp"},
            {"trade_date": day, "session": "pm", "valid_for_admission": valid, "config_fingerprint": "fp"},
        ]

    def test_a16_m5_streak_reset_on_incomplete_day(self):
        # A16：某交易日任一 invalid 轮次 -> 该日整体无效，有效连续日重新计算；
        # 周末（09-12/09-13）不打断交易日连续性
        records = []
        records += self._day_records("2026-09-08", valid=True)
        records += self._day_records("2026-09-09", valid=True)
        records += self._day_records("2026-09-10", valid=True)
        # 09-11（周五）am valid + pm invalid -> 当日整体无效
        records.append({"trade_date": "2026-09-11", "session": "am", "valid_for_admission": True, "config_fingerprint": "fp"})
        records.append({"trade_date": "2026-09-11", "session": "pm", "valid_for_admission": False, "config_fingerprint": "fp"})
        records += self._day_records("2026-09-14", valid=True)
        records += self._day_records("2026-09-15", valid=True)
        self.assertEqual(pl.compute_m5_streak(records, "2026-09-15"), 2)


if __name__ == "__main__":
    unittest.main()
