"""23 v2 bulk 适配器 + 候选并集 + shadow 真值对照测试。"""
import unittest
from datetime import datetime, timezone, timedelta

import pandas as pd

from backend.plugins.principal_capital.sources import sina_market as sm
from backend.plugins.principal_capital import pipeline as pl

BEIJING_TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 15, 14, 35, tzinfo=BEIJING_TZ)


def _bulk_item(symbol="sh600000", ratio=0.5, r0_ratio=0.3, r0_net=1e6, amount=1e8, changeratio=0.02):
    return {"symbol": symbol, "name": "测试股", "trade": "10.0", "changeratio": changeratio,
            "amount": amount, "r0_net": r0_net, "r0_ratio": r0_ratio,
            "r3_net": 1, "netamount": 1e6, "ratioamount": ratio}


def _refined(code, ratio):
    main_net = 2e8 * ratio / 100
    return {"code": code, "name": f"股票{code}", "price": 10.0, "change_pct": 2.0,
            "total_amount": 2e8, "main_net_inflow": main_net, "main_inflow_ratio": ratio,
            "super_net": main_net * 0.6, "big_net": main_net * 0.4, "mid_net": 0, "small_net": -main_net}


class BulkParseTest(unittest.TestCase):
    def test_parse_normalizes_percent_once(self):
        rows = sm.parse_bulk_rows([_bulk_item(ratio=0.4, r0_ratio=0.2)], NOW)
        self.assertEqual(rows[0]["ratioamount"], 40.0)
        self.assertEqual(rows[0]["super_ratio"], 20.0)
        self.assertAlmostEqual(rows[0]["change_pct"], 2.0)
        self.assertIn("coarse_candidate", rows[0]["eligible_for"])

    def test_parse_non_list_raises(self):
        with self.assertRaises(ValueError):
            sm.parse_bulk_rows({"a": 1}, NOW)

    def test_parse_missing_and_invalid_values_not_zero(self):
        item = _bulk_item()
        item["amount"] = "-"
        item["ratioamount"] = None
        item["r0_net"] = "NaN"
        rows = sm.parse_bulk_rows([item], NOW)
        self.assertIsNone(rows[0]["total_amount"])
        self.assertIsNone(rows[0]["ratioamount"])
        self.assertIsNone(rows[0]["super_net"])
        self.assertEqual(rows[0]["eligible_for"], [])
        self.assertTrue(any("non_finite" in reason for reason in rows[0]["degraded_reasons"]))

    def test_parse_skips_non_shsz_symbols(self):
        rows = sm.parse_bulk_rows([_bulk_item(symbol="bj920001")], NOW)
        self.assertEqual(rows, [])


class BulkValidationTest(unittest.TestCase):
    def test_full_coverage_valid(self):
        rows = sm.parse_bulk_rows([_bulk_item("sh600000"), _bulk_item("sz000001")], NOW)
        result = sm.validate_bulk_rows(rows, ["600000", "000001"])
        self.assertTrue(result["valid"])
        self.assertEqual(result["coverage_ratio"], 1.0)

    def test_missing_one_code_invalid(self):
        rows = sm.parse_bulk_rows([_bulk_item("sh600000")], NOW)
        result = sm.validate_bulk_rows(rows, ["600000", "000001"])
        self.assertFalse(result["valid"])
        self.assertIn("000001", result["missing_codes"])

    def test_extra_index_fund_allowed(self):
        rows = sm.parse_bulk_rows([_bulk_item("sh600000"), _bulk_item("sh000001")], NOW)
        result = sm.validate_bulk_rows(rows, ["600000"])
        self.assertTrue(result["valid"])
        self.assertIn("000001", result["extra_codes"])

    def test_duplicate_invalid(self):
        rows = sm.parse_bulk_rows([_bulk_item("sh600000"), _bulk_item("sh600000")], NOW)
        result = sm.validate_bulk_rows(rows, ["600000"])
        self.assertFalse(result["valid"])
        self.assertIn("600000", result["duplicate_codes"])

    def test_known_non_trading_reason(self):
        rows = sm.parse_bulk_rows([_bulk_item("sh600000")], NOW)
        result = sm.validate_bulk_rows(rows, ["600000", "000001"], known_non_trading={"000001": "停牌"})
        self.assertTrue(result["valid"])
        self.assertEqual(result["missing_reasons"]["000001"], "停牌")


class CoarseUnionTest(unittest.TestCase):
    def test_union_dedup_stable_order(self):
        rows = sm.parse_bulk_rows([
            _bulk_item("sh600000", ratio=0.6),   # ratioamount_buy
            _bulk_item("sz000001", ratio=-0.3),  # ratioamount_sell
            _bulk_item("sh600002", ratio=0.1, r0_net=1e9),  # r0_net_top
        ], NOW)
        result = pl.build_coarse_union(rows, ["600003"], ["600004"], ["600005"], {})
        self.assertEqual(result["codes"], ["000001", "600000", "600002", "600003", "600004", "600005"])
        self.assertIn("ratioamount_buy", result["reasons"]["600000"])

    def test_previous_dwell_audit_enter(self):
        rows = sm.parse_bulk_rows([_bulk_item("sh600000", ratio=0.1)], NOW)
        result = pl.build_coarse_union(rows, ["600001"], ["600002"], ["600003"], {})
        self.assertIn("600001", result["codes"])
        self.assertIn("600002", result["codes"])
        self.assertIn("600003", result["codes"])


class CompareWithTruthTest(unittest.TestCase):
    def _filters(self):
        from backend.plugins.principal_capital.service import filter_buy_candidates, filter_sell_candidates
        return {
            "buy": lambda df: filter_buy_candidates(df),
            "sell": lambda df: filter_sell_candidates(df),
        }

    def test_false_negative_buy_detected(self):
        # ratioamount 粗筛低但精算 main_inflow_ratio >= 50 -> 必须被标 false negative
        df = pd.DataFrame([_refined("600001", 55), _refined("600002", 30)])
        coarse = ["600002"]  # 漏掉 600001
        audit = pl.compare_with_truth(coarse, df, self._filters())
        self.assertEqual(audit["false_negative_buy"], ["600001"])
        self.assertEqual(audit["truth_buy_count"], 1)

    def test_false_negative_sell_detected(self):
        df = pd.DataFrame([_refined("600001", -35), _refined("600002", 10)])
        coarse = ["600002"]
        audit = pl.compare_with_truth(coarse, df, self._filters())
        self.assertEqual(audit["false_negative_sell"], ["600001"])

    def test_no_false_negative_when_union_covers(self):
        df = pd.DataFrame([_refined("600001", 55), _refined("600002", -35)])
        audit = pl.compare_with_truth(["600001", "600002"], df, self._filters())
        self.assertEqual(audit["false_negative_buy"], [])
        self.assertEqual(audit["false_negative_sell"], [])


class PipelineHelpersTest(unittest.TestCase):
    def test_round_context_requires_aware(self):
        with self.assertRaises(ValueError):
            pl.build_round_context("o", "official", "strict", "2026-09-15", datetime(2026, 9, 15, 14, 30), datetime(2026, 9, 15, 14, 31))
        ctx = pl.build_round_context("o", "official", "strict", "2026-09-15", NOW, NOW + timedelta(minutes=1))
        self.assertEqual(ctx["pipeline_mode"], "strict")

    def test_auto_fallback(self):
        audit = {"false_negative_buy": ["600001"], "false_negative_sell": []}
        decision = pl.evaluate_auto_fallback(audit, None, 1.0, True)
        self.assertTrue(decision["should_fallback"])
        self.assertIn("sentinel_false_negative", decision["reasons"])

    def test_complement_audit_deterministic(self):
        codes = [f"600{i:03d}" for i in range(100)]
        coarse = set(codes[:20])
        a, _ = pl.complement_audit_codes(codes, coarse, 0, {"complement_audit_size": 20})
        b, _ = pl.complement_audit_codes(codes, coarse, 0, {"complement_audit_size": 20})
        self.assertEqual(a, b)
        self.assertEqual(len(a), 20)
        self.assertTrue(set(a).isdisjoint(coarse))

    def test_complement_audit_exact_n_and_cursor_advance(self):
        # A04：3200 个代码、size=300 -> 恰好 300；游标推进；多轮覆盖不同补集
        codes = [f"{100000 + i:06d}" for i in range(3200)]
        coarse = set(codes[:1000])
        cursor = 0
        seen = []
        for _round in range(3):
            picked, cursor = pl.complement_audit_codes(codes, coarse, cursor, {"complement_audit_size": 300})
            self.assertEqual(len(picked), 300)
            self.assertTrue(set(picked).isdisjoint(coarse))
            seen.extend(picked)
        self.assertGreater(len(set(seen)), 300)  # 多轮覆盖不同补集


if __name__ == "__main__":
    unittest.main()
