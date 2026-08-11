"""主力资金 service 单元测试。"""
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from backend.plugins.principal_capital import service as pcs

BEIJING_TZ = timezone(timedelta(hours=8))


def _row(code, name, ratio, amount=2e8, change_pct=3.0):
    main_net = amount * ratio / 100
    return {
        "code": code, "name": name, "price": 10.0, "change_pct": change_pct,
        "total_amount": amount, "main_net_inflow": main_net,
        "main_inflow_ratio": ratio,
        "super_net": main_net * 0.6, "big_net": main_net * 0.4,
        "mid_net": 0, "small_net": -main_net, "source": "eastmoney",
    }


class PrincipalCapitalServiceTest(unittest.TestCase):

    def test_read_report_resilient_returns_remote_non_completed_statuses(self):
        for status in ("no_data", "skipped", "error"):
            with self.subTest(status=status), \
                 patch.object(pcs, "read_report", return_value={"status": "empty"}), \
                 patch.object(
                     pcs,
                     "_fetch_snapshot_json",
                     return_value={"status": status, "now": "2026-08-11T09:45:00+08:00"},
                 ):
                result = pcs.read_report_resilient()

            self.assertEqual(result["status"], status)
            self.assertEqual(result["_source"], "snapshot")
            self.assertEqual(result["now"], "2026-08-11T09:45:00+08:00")
            self.assertIn("reason", result)
            self.assertIn("source_status", result)

    def test_read_report_resilient_keeps_local_completed_report_first(self):
        local = {"status": "completed", "now": "2026-08-11T09:40:00+08:00"}
        with patch.object(pcs, "read_report", return_value=local), \
             patch.object(pcs, "_fetch_snapshot_json") as fetch_snapshot:
            result = pcs.read_report_resilient()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["now"], local["now"])
        self.assertIn("reason", result)
        self.assertIn("source_status", result)
        fetch_snapshot.assert_not_called()

    def test_filter_buy_candidates_hits_threshold(self):
        df = pd.DataFrame([_row("600001", "主板一号", 55), _row("600002", "主板二号", 49)])
        result = pcs.filter_buy_candidates(df)
        self.assertEqual(result["code"].tolist(), ["600001"])

    def test_filter_buy_candidates_below_threshold_returns_empty(self):
        df = pd.DataFrame([_row("600001", "主板一号", 45)])
        self.assertTrue(pcs.filter_buy_candidates(df).empty)

    def test_filter_sell_candidates_hits_threshold(self):
        df = pd.DataFrame([_row("600001", "主板一号", -35), _row("600002", "主板二号", -20)])
        result = pcs.filter_sell_candidates(df)
        self.assertEqual(result["code"].tolist(), ["600001"])
        self.assertEqual(pcs.classify_sell_severity(-35), "warn")

    def test_classify_sell_severity_levels(self):
        self.assertEqual(pcs.classify_sell_severity(-32), "warn")
        self.assertEqual(pcs.classify_sell_severity(-45), "alert")
        self.assertEqual(pcs.classify_sell_severity(-55), "danger")

    def test_board_and_st_filters(self):
        df = pd.DataFrame([
            _row("300001", "创业板", 60),
            _row("301001", "创业板二", 60),
            _row("688001", "科创板", 60),
            _row("600001", "ST风险", 60),
            _row("600002", "正常主板", 60),
        ])
        self.assertEqual(pcs.filter_buy_candidates(df)["code"].tolist(), ["600002"])

    def test_missing_ratio_filtered_out(self):
        row = _row("600001", "主板一号", 60)
        row["main_inflow_ratio"] = None
        self.assertTrue(pcs.filter_buy_candidates(pd.DataFrame([row])).empty)

    def test_min_amount_filtered_out(self):
        df = pd.DataFrame([_row("600001", "主板一号", 60, amount=5e6)])
        self.assertTrue(pcs.filter_buy_candidates(df).empty)

    def test_non_trading_time_skipped(self):
        now = datetime(2026, 6, 29, 8, 50, tzinfo=BEIJING_TZ)
        with TemporaryDirectory() as tmp:
            with patch.object(pcs, "DATA_DIR", Path(tmp)), \
                 patch.object(pcs, "REPORT_DIR", Path(tmp)), \
                 patch.object(pcs, "REPORT_FILE", Path(tmp) / "latest.json"), \
                 patch.object(pcs, "HISTORY_FILE", Path(tmp) / "history.json"):
                result = pcs.run_principal_capital_scan(now=now)
        self.assertEqual(result["status"], "skipped")

    def test_no_data_status(self):
        with TemporaryDirectory() as tmp:
            with patch.object(pcs, "DATA_DIR", Path(tmp)), \
                 patch.object(pcs, "REPORT_DIR", Path(tmp)), \
                 patch.object(pcs, "REPORT_FILE", Path(tmp) / "latest.json"), \
                 patch.object(pcs, "HISTORY_FILE", Path(tmp) / "history.json"), \
                 patch.object(pcs, "fetch_market_fund_flow_resilient",
                              return_value=(pd.DataFrame(), {"active_source": "none"})):
                result = pcs.run_principal_capital_scan(
                    now=datetime(2026, 6, 29, 10, 0, tzinfo=BEIJING_TZ), force=True)
        self.assertEqual(result["status"], "no_data")

    def test_buy_dedup_same_day(self):
        df = pd.DataFrame([_row("600001", "主板一号", 60), _row("600002", "主板二号", 70)])
        with TemporaryDirectory() as tmp:
            with patch.object(pcs, "DATA_DIR", Path(tmp)), \
                 patch.object(pcs, "REPORT_DIR", Path(tmp)), \
                 patch.object(pcs, "REPORT_FILE", Path(tmp) / "latest.json"), \
                 patch.object(pcs, "HISTORY_FILE", Path(tmp) / "history.json"), \
                 patch.object(pcs, "fetch_market_fund_flow_resilient",
                              return_value=(df, {"active_source": "eastmoney"})), \
                 patch.object(pcs, "send_email", return_value=(True, None)):
                now = datetime(2026, 6, 29, 10, 0, tzinfo=BEIJING_TZ)
                first = pcs.run_principal_capital_scan(now=now, force=True)
                second = pcs.run_principal_capital_scan(now=now + timedelta(minutes=5), force=True)
        self.assertEqual(first["buy_fresh_count"], 2)
        self.assertEqual(second["buy_fresh_count"], 0)

    def test_sell_cooldown(self):
        df = pd.DataFrame([_row("600001", "主板一号", -35)])
        with TemporaryDirectory() as tmp:
            with patch.object(pcs, "DATA_DIR", Path(tmp)), \
                 patch.object(pcs, "REPORT_DIR", Path(tmp)), \
                 patch.object(pcs, "REPORT_FILE", Path(tmp) / "latest.json"), \
                 patch.object(pcs, "HISTORY_FILE", Path(tmp) / "history.json"), \
                 patch.object(pcs, "fetch_market_fund_flow_resilient",
                              return_value=(df, {"active_source": "eastmoney"})), \
                 patch.object(pcs, "send_email", return_value=(True, None)):
                t1 = pcs.run_principal_capital_scan(
                    now=datetime(2026, 6, 29, 10, 0, tzinfo=BEIJING_TZ), force=True)
                t2 = pcs.run_principal_capital_scan(
                    now=datetime(2026, 6, 29, 10, 30, tzinfo=BEIJING_TZ), force=True)
                t3 = pcs.run_principal_capital_scan(
                    now=datetime(2026, 6, 29, 11, 5, tzinfo=BEIJING_TZ), force=True)
        self.assertEqual(t1["sell_fresh_count"], 1)
        self.assertEqual(t2["sell_fresh_count"], 0)
        self.assertEqual(t3["sell_fresh_count"], 1)

    def test_build_email_payload_contains_buy_and_sell_counts(self):
        buy = pd.DataFrame([_row("600001", "主板一号", 60)])
        sell = pd.DataFrame([{**_row("600002", "主板二号", -55), "severity": "danger"}])
        subject, text, html = pcs.build_email_payload(
            buy, sell,
            datetime(2026, 6, 29, 10, 0, tzinfo=BEIJING_TZ),
            {"active_source": "eastmoney", "is_stale": False},
        )
        self.assertIn("买1卖1", subject)
        self.assertIn("买入区", text)
        self.assertIn("卖出区", text)
        self.assertIn("danger", html.lower())


if __name__ == "__main__":
    unittest.main()
