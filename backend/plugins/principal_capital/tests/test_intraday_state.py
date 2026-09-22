"""23 v2 日内状态纯函数测试。"""
import unittest
from datetime import datetime, timedelta, timezone

from backend.plugins.principal_capital import intraday_state as its

BEIJING_TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 15, 14, 35, tzinfo=BEIJING_TZ)


def _row(code, ratio=60.0, net=6e7, amount=2e8):
    return {"code": code, "name": f"股票{code}", "price": 10.0, "change_pct": 2.0,
            "total_amount": amount, "main_net_inflow": net, "main_inflow_ratio": ratio,
            "super_net": net * 0.6, "big_net": net * 0.4, "mid_net": 0, "small_net": -net,
            "source": "sina_single", "quality_status": "provisional"}


def _obs(ts, main_net, total_amount, segment="sina_single:v1:2026-09-15", partial=False, stale=False, cache=False):
    return {"observed_at": ts.isoformat(), "source_segment": segment, "main_net_inflow": main_net,
            "total_amount": total_amount, "batch_id": "b", "partial": partial, "stale": stale, "cache": cache}


class CandidateStateTest(unittest.TestCase):
    def test_first_seen_fresh_then_fresh_false(self):
        meta = {"batch_id": "b1", "now": NOW.isoformat(), "is_partial": False}
        first = its.merge_candidate_state(None, [_row("600001")], [], meta)
        self.assertTrue(first["buy:600001"]["fresh"])
        self.assertEqual(first["buy:600001"]["seen_rounds"], 1)

        meta2 = {"batch_id": "b2", "now": (NOW + timedelta(minutes=5)).isoformat(), "is_partial": False}
        second = its.merge_candidate_state({"candidates": first}, [_row("600001", ratio=62.0)], [], meta2)
        self.assertFalse(second["buy:600001"]["fresh"])
        self.assertEqual(second["buy:600001"]["seen_rounds"], 2)
        self.assertEqual(second["buy:600001"]["latest_metrics"]["main_inflow_ratio"], 62.0)

    def test_complete_batch_marks_missing_as_not_current(self):
        meta = {"batch_id": "b1", "now": NOW.isoformat(), "is_partial": False}
        first = its.merge_candidate_state(None, [_row("600001"), _row("600002")], [], meta)
        meta2 = {"batch_id": "b2", "now": (NOW + timedelta(minutes=5)).isoformat(), "is_partial": False}
        second = its.merge_candidate_state({"candidates": first}, [_row("600001")], [], meta2)
        self.assertTrue(second["buy:600001"]["is_current"])
        self.assertFalse(second["buy:600002"]["is_current"])

    def test_partial_batch_does_not_evict(self):
        meta = {"batch_id": "b1", "now": NOW.isoformat(), "is_partial": False}
        first = its.merge_candidate_state(None, [_row("600001"), _row("600002")], [], meta)
        meta2 = {"batch_id": "b2", "now": (NOW + timedelta(minutes=5)).isoformat(), "is_partial": True}
        second = its.merge_candidate_state({"candidates": first}, [_row("600001")], [], meta2)
        self.assertTrue(second["buy:600002"]["is_current"])

    def test_same_batch_replay_is_idempotent(self):
        meta = {"batch_id": "b1", "now": NOW.isoformat(), "is_partial": False}
        first = its.merge_candidate_state(None, [_row("600001")], [], meta)
        replay = its.merge_candidate_state({"candidates": first}, [_row("600001")], [], meta)
        self.assertEqual(replay["buy:600001"]["seen_rounds"], 1)

    def test_cross_trade_date_resets(self):
        state = its.empty_state("2026-09-15")
        state["candidates"] = {"buy:600001": {"is_current": True}}
        state["summary_state"] = {"am": {"status": "sent"}, "pm": {"status": "pending"}}
        reset = its.reset_state_for_trade_date(state, "2026-09-16")
        self.assertEqual(reset["candidates"], {})
        self.assertEqual(reset["summary_state"], {"am": {"status": "not_attempted"}, "pm": {"status": "not_attempted"}})
        self.assertEqual(reset["audit_cursor"], 0)


class OwnerTest(unittest.TestCase):
    def test_acquire_owner_and_renew(self):
        state = its.empty_state("2026-09-15")
        ok, state, reason = its.acquire_owner(state, "github_actions", 600, NOW)
        self.assertTrue(ok)
        self.assertIsNone(reason)
        ok2, state2, _ = its.acquire_owner(state, "github_actions", 600, NOW + timedelta(minutes=5))
        self.assertTrue(ok2)
        self.assertEqual(state2["owner_id"], "github_actions")

    def test_owner_conflict_rejected(self):
        state = its.empty_state("2026-09-15")
        ok, state, _ = its.acquire_owner(state, "github_actions", 600, NOW)
        self.assertTrue(ok)
        ok2, _, reason = its.acquire_owner(state, "other_owner", 600, NOW + timedelta(minutes=1))
        self.assertFalse(ok2)
        self.assertIn("owner_conflict", reason)

    def test_lease_expired_allows_takeover(self):
        state = its.empty_state("2026-09-15")
        ok, state, _ = its.acquire_owner(state, "github_actions", 60, NOW)
        self.assertTrue(ok)
        ok2, _, _ = its.acquire_owner(state, "github_actions", 600, NOW + timedelta(minutes=11))
        self.assertTrue(ok2)


class FundObservationTest(unittest.TestCase):
    def test_append_caps_points(self):
        cfg = {"intraday_max_points": 3}
        series = {}
        for i in range(5):
            meta = {"batch_id": f"b{i}", "observed_at": NOW.isoformat(),
                    "source_segment": "sina_single:v1:2026-09-15"}
            series = its.append_fund_observation(
                series, {"code": "600001", "main_net_inflow": 100 + i, "total_amount": 1000 + i}, meta, cfg)
        self.assertEqual(len(series["600001"]), 3)
        self.assertEqual(series["600001"][-1]["main_net_inflow"], 104)

    def test_append_idempotent_by_batch(self):
        series = {}
        meta = {"batch_id": "b1", "observed_at": NOW.isoformat()}
        row = {"code": "600001", "main_net_inflow": 100, "total_amount": 1000}
        series = its.append_fund_observation(series, row, meta, {})
        series = its.append_fund_observation(series, row, meta, {})
        self.assertEqual(len(series["600001"]), 1)

    def test_append_does_not_mutate_input(self):
        series = {}
        meta = {"batch_id": "b", "observed_at": NOW.isoformat()}
        its.append_fund_observation(series, {"code": "600001", "main_net_inflow": 1, "total_amount": 10}, meta, {})
        self.assertEqual(series, {})


class IntradayFeaturesTest(unittest.TestCase):
    def test_consecutive_five_minute_diff(self):
        series = [
            _obs(NOW - timedelta(minutes=10), 100, 1000),
            _obs(NOW - timedelta(minutes=5), 130, 1300),
            _obs(NOW, 175, 1600),
        ]
        feat = its.compute_intraday_features(series, NOW)
        # 30 分钟窗口覆盖不足 -> warming + roll None，但 5 分钟增量仍可计算
        self.assertTrue(feat["warming"])
        self.assertEqual(feat["warming_reason"], "insufficient_coverage")
        self.assertEqual(feat["acc_win"], 2)
        self.assertAlmostEqual(feat["inc_ratio_5m"], 15.0)  # 45 / 300 * 100
        self.assertIsNone(feat["roll_net_30m"])

    def test_roll_net_30m_computed_when_full_window(self):
        series = [
            _obs(NOW - timedelta(minutes=30), 100, 1000),
            _obs(NOW - timedelta(minutes=25), 130, 1300),
            _obs(NOW - timedelta(minutes=20), 160, 1600),
            _obs(NOW - timedelta(minutes=15), 190, 1900),
            _obs(NOW - timedelta(minutes=10), 220, 2200),
            _obs(NOW - timedelta(minutes=5), 250, 2500),
            _obs(NOW, 280, 2800),
        ]
        feat = its.compute_intraday_features(series, NOW)
        self.assertFalse(feat["warming"])
        self.assertAlmostEqual(feat["roll_net_30m"], 180.0)  # 6 * 30

    def test_source_changed_warming(self):
        series = [
            _obs(NOW - timedelta(minutes=5), 100, 1000, segment="sina_single:v1:2026-09-15"),
            _obs(NOW, 130, 1300, segment="tencent:v1:2026-09-15"),
        ]
        feat = its.compute_intraday_features(series, NOW)
        self.assertTrue(feat["warming"])
        self.assertEqual(feat["warming_reason"], "source_changed")

    def test_gap_too_large_warming(self):
        series = [_obs(NOW - timedelta(minutes=12), 100, 1000), _obs(NOW, 130, 1300)]
        feat = its.compute_intraday_features(series, NOW)
        self.assertTrue(feat["warming"])
        self.assertEqual(feat["warming_reason"], "gap_too_large")

    def test_counter_reset_warming(self):
        series = [_obs(NOW - timedelta(minutes=5), 100, 1500), _obs(NOW, 130, 1300)]
        feat = its.compute_intraday_features(series, NOW)
        self.assertTrue(feat["warming"])
        self.assertEqual(feat["warming_reason"], "counter_reset")

    def test_partial_or_cache_obs_excluded(self):
        series = [
            _obs(NOW - timedelta(minutes=5), 100, 1000),
            _obs(NOW, 130, 1300, partial=True),
        ]
        feat = its.compute_intraday_features(series, NOW)
        self.assertTrue(feat["warming"])
        self.assertEqual(feat["warming_reason"], "batch_partial")

    def test_invalid_obs_between_valid_breaks_run(self):
        # A12：invalid 点位于两个有效点之间，不得跨 invalid 点配对
        series = [
            _obs(NOW - timedelta(minutes=10), 100, 1000),
            _obs(NOW - timedelta(minutes=5), 999, 999, partial=True),
            _obs(NOW, 200, 2000),
        ]
        feat = its.compute_intraday_features(series, NOW)
        self.assertTrue(feat["warming"])

    def test_roll_none_when_coverage_insufficient(self):
        series = [_obs(NOW - timedelta(minutes=9), 100, 1000), _obs(NOW, 130, 1300)]
        feat = its.compute_intraday_features(series, NOW)
        self.assertTrue(feat["warming"])
        self.assertIsNone(feat["roll_net_30m"])


class RadarPoolTest(unittest.TestCase):
    def test_partial_keeps_old_members_except_stale(self):
        prev = {
            "600001": {"code": "600001", "last_seen_at": NOW.isoformat(),
                       "dwell_until": (NOW + timedelta(minutes=10)).isoformat()},
            "600002": {"code": "600002", "last_seen_at": (NOW - timedelta(minutes=20)).isoformat(),
                       "dwell_until": (NOW + timedelta(minutes=10)).isoformat()},
        }
        result = its.select_radar_pool(prev, None, NOW, {})
        self.assertIn("600001", result)
        self.assertNotIn("600002", result)

    def test_rotation_seats_reserved(self):
        prev = {}
        for i in range(50):
            code = f"600{i:03d}"
            prev[code] = {"code": code, "entered_at": NOW.isoformat(), "last_seen_at": NOW.isoformat(),
                          "dwell_until": (NOW + timedelta(minutes=30)).isoformat(),
                          "latest_metrics": {"main_inflow_ratio": 50 + i, "main_net_inflow": 1e7 + i}}
        rows = [{"code": f"700{i:03d}", "main_inflow_ratio": 80, "main_net_inflow": 1e8} for i in range(10)]
        cfg = {"radar_pool_max": 40, "radar_pool_min_dwell_min": 30, "radar_pool_protected_cap": 30,
               "radar_pool_rotation_seats": 10, "radar_pool_max_stale_min": 15}
        result = its.select_radar_pool(prev, rows, NOW, cfg)
        self.assertLessEqual(len(result), 40)
        fresh_in = sum(1 for entry in result.values() if entry["selection_reason"] != "protected_dwell")
        self.assertGreaterEqual(fresh_in, 1)

    def test_latest_metrics_attached(self):
        rows = [{"code": "600001", "main_inflow_ratio": 66, "main_net_inflow": 6e7, "total_amount": 2e8}]
        result = its.select_radar_pool({}, rows, NOW, {})
        self.assertIn("600001", result)
        self.assertEqual(result["600001"]["latest_metrics"]["main_inflow_ratio"], 66)


class SentinelFinalizerTest(unittest.TestCase):
    def test_should_run_sentinel(self):
        state = {}
        self.assertEqual(its.should_run_sentinel(state, datetime(2026, 9, 15, 9, 36, tzinfo=BEIJING_TZ), ["09:35", "10:30"]), "09:35")
        done = its.mark_sentinel_done(state, "09:35")
        self.assertIsNone(its.should_run_sentinel(done, datetime(2026, 9, 15, 9, 40, tzinfo=BEIJING_TZ), ["09:35", "10:30"]))

    def _latest(self, now_iso, batch_id="b1"):
        return {
            "status": "completed", "now": now_iso,
            "quality": {"notify_eligible": True},
            "batch_id": batch_id, "trade_date": "2026-09-15",
        }

    def test_finalize_already_sent(self):
        state = its.empty_state("2026-09-15")
        state["summary_state"]["am"] = {"status": "sent"}
        state["last_batch_id"] = "b1"
        decision = its.should_finalize_session(state, "am", self._latest(NOW.isoformat()), NOW, {})
        self.assertFalse(decision["should_send"])
        self.assertEqual(decision["reason"], "already_sent")

    def test_finalize_delivery_unknown_blocks(self):
        state = its.empty_state("2026-09-15")
        state["summary_state"]["am"] = {"status": "delivery_unknown"}
        decision = its.should_finalize_session(state, "am", self._latest(NOW.isoformat()), NOW, {})
        self.assertFalse(decision["should_send"])
        self.assertEqual(decision["reason"], "delivery_unknown")

    def test_finalize_stale_batch(self):
        state = its.empty_state("2026-09-15")
        state["last_batch_id"] = "b1"
        old = (NOW - timedelta(minutes=20)).isoformat()
        decision = its.should_finalize_session(state, "am", self._latest(old), NOW, {"summary_max_age_min": 15})
        self.assertFalse(decision["should_send"])
        self.assertEqual(decision["skipped_reason"], "latest_batch_stale")

    def test_finalize_notify_not_eligible(self):
        state = its.empty_state("2026-09-15")
        state["last_batch_id"] = "b1"
        latest = self._latest(NOW.isoformat())
        latest["quality"]["notify_eligible"] = False
        decision = its.should_finalize_session(state, "am", latest, NOW, {})
        self.assertFalse(decision["should_send"])
        self.assertEqual(decision["reason"], "notify_not_eligible")

    def test_finalize_batch_mismatch(self):
        state = its.empty_state("2026-09-15")
        state["last_batch_id"] = "b1"
        latest = self._latest(NOW.isoformat(), batch_id="b2")
        decision = its.should_finalize_session(state, "am", latest, NOW, {})
        self.assertFalse(decision["should_send"])
        self.assertEqual(decision["reason"], "batch_mismatch")

    def test_finalize_ready(self):
        state = its.empty_state("2026-09-15")
        state["last_batch_id"] = "b1"
        decision = its.should_finalize_session(state, "am", self._latest(NOW.isoformat()), NOW, {"summary_max_age_min": 15})
        self.assertTrue(decision["should_send"])


if __name__ == "__main__":
    unittest.main()
