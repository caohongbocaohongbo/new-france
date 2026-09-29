"""25 午间/收盘 summary finalizer 状态机测试（显式 session 绑定 + CAS 抢占 + 幂等 + 可恢复）。"""
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


def _snapshot(session="am", status="completed", quality_status="accepted", batch_id="b1"):
    return {
        "snapshot_id": batch_id, "batch_id": batch_id,
        "captured_at": NOW.isoformat(), "now": NOW.isoformat(),
        "trade_date": "2026-09-15", "session": session,
        "status": status, "quality": {"status": quality_status}, "scanned": 100,
    }


def _write_state(state_file, owner=OWNER, batch_id="b1", quality_status="accepted",
                 status="completed", session="am"):
    state = its.empty_state("2026-09-15")
    ok, state, _ = its.acquire_owner(state, owner, 600, NOW)
    assert ok
    state["last_batch_id"] = batch_id
    state["session_snapshots"] = {
        session: _snapshot(session=session, status=status,
                           quality_status=quality_status, batch_id=batch_id),
    }
    state_file.parent.mkdir(parents=True, exist_ok=True)
    its.save_state(state, path=state_file)


class SessionFinalizerTest(unittest.TestCase):
    def _patches(self, tmp):
        state_file = Path(tmp) / "intraday_state.json"
        return (
            patch.object(pcs.intraday, "INTRADAY_STATE_FILE", state_file),
            patch("backend.services.trading_calendar.is_trading_day", return_value=True),
        ), state_file

    def test_official_sends_once(self):
        with TemporaryDirectory() as tmp:
            patches, state_file = self._patches(tmp)
            _write_state(state_file)
            for p in patches:
                p.start()
            try:
                with patch.object(pcs, "send_email", return_value=(True, None)):
                    first = pcs.finalize_principal_capital_session(
                        "am", now=NOW, execution_mode="official", owner_id=OWNER)
                state = its.load_state_raw(path=state_file)
            finally:
                for p in patches:
                    p.stop()
        self.assertEqual(first["status"], "completed")
        self.assertTrue(first["email_sent"])
        self.assertEqual(its.get_summary_job(state, "am")["status"], "completed")

    def test_provisional_skips_when_not_allowed(self):
        with TemporaryDirectory() as tmp:
            patches, state_file = self._patches(tmp)
            _write_state(state_file, quality_status="provisional")
            for p in patches:
                p.start()
            try:
                with patch.object(pcs, "send_email", return_value=(True, None)) as send:
                    result = pcs.finalize_principal_capital_session(
                        "am", now=NOW, execution_mode="official", owner_id=OWNER)
                state = its.load_state_raw(path=state_file)
            finally:
                for p in patches:
                    p.stop()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], its.SummaryReason.NOTIFY_NOT_ELIGIBLE)
        send.assert_not_called()
        self.assertEqual(its.get_summary_job(state, "am")["status"], "skipped")

    def test_provisional_allowed_completes(self):
        # P1：PC_ALLOW_PROVISIONAL_NOTIFY=1 时 provisional 应能发摘要（READY_PROVISIONAL）
        with TemporaryDirectory() as tmp:
            patches, state_file = self._patches(tmp)
            _write_state(state_file, quality_status="provisional")
            for p in patches:
                p.start()
            try:
                with patch.dict(pcs.CONFIG, {"allow_provisional_notify": True}):
                    with patch.object(pcs, "send_email", return_value=(True, None)):
                        result = pcs.finalize_principal_capital_session(
                            "am", now=NOW, execution_mode="official", owner_id=OWNER)
            finally:
                for p in patches:
                    p.stop()
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["email_sent"])

    def test_no_snapshot_is_retryable(self):
        # P0：NO_VALID_SNAPSHOT 不应进入终态 skipped，而应 retry_wait（snapshot 可能稍后就绪）
        with TemporaryDirectory() as tmp:
            patches, state_file = self._patches(tmp)
            state = its.empty_state("2026-09-15")
            its.save_state(state, path=state_file)
            for p in patches:
                p.start()
            try:
                result = pcs.finalize_principal_capital_session(
                    "am", now=NOW, execution_mode="official", owner_id=OWNER)
                state = its.load_state_raw(path=state_file)
            finally:
                for p in patches:
                    p.stop()
        self.assertEqual(result["status"], "retry_wait")
        self.assertEqual(result["reason"], its.SummaryReason.NO_VALID_SNAPSHOT)
        self.assertEqual(its.get_summary_job(state, "am")["status"], "retry_wait")

    def test_pm_snapshot_not_used_for_am(self):
        # PM snapshot 不能供 AM finalizer 使用（§6/§7 显式绑定）
        with TemporaryDirectory() as tmp:
            patches, state_file = self._patches(tmp)
            _write_state(state_file, session="pm")
            for p in patches:
                p.start()
            try:
                with patch.object(pcs, "send_email", return_value=(True, None)) as send:
                    result = pcs.finalize_principal_capital_session(
                        "am", now=NOW, execution_mode="official", owner_id=OWNER)
            finally:
                for p in patches:
                    p.stop()
        self.assertEqual(result["reason"], its.SummaryReason.NO_VALID_SNAPSHOT)
        send.assert_not_called()

    def test_eligibility_session_mismatch(self):
        state = its.empty_state("2026-09-15")
        pm_snapshot = _snapshot(session="pm")
        decision = its.summary_eligibility(state, "am", pm_snapshot, NOW, {})
        self.assertFalse(decision["eligible"])
        self.assertEqual(decision["reason"], its.SummaryReason.SESSION_MISMATCH)

    def test_shadow_constructs_without_sending(self):
        with TemporaryDirectory() as tmp:
            patches, state_file = self._patches(tmp)
            _write_state(state_file)
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

    def test_delivery_unknown_blocks_resend(self):
        with TemporaryDirectory() as tmp:
            patches, state_file = self._patches(tmp)
            _write_state(state_file)
            for p in patches:
                p.start()
            try:
                with patch.object(pcs, "send_email", side_effect=RuntimeError("smtp boom")):
                    first = pcs.finalize_principal_capital_session(
                        "am", now=NOW, execution_mode="official", owner_id=OWNER)
                # 下一次调用（重试未到 next_retry_at）不得自动重发
                with patch.object(pcs, "send_email", return_value=(True, None)) as send:
                    second = pcs.finalize_principal_capital_session(
                        "am", now=NOW, execution_mode="official", owner_id=OWNER)
            finally:
                for p in patches:
                    p.stop()
        self.assertEqual(first["status"], "delivery_unknown")
        self.assertEqual(first["reason"], its.SummaryReason.DELIVERY_UNKNOWN)
        self.assertEqual(second["status"], "skipped")
        self.assertEqual(second["reason"], its.SummaryReason.DELIVERY_UNKNOWN)
        send.assert_not_called()

    def test_non_trading_day_skips(self):
        with TemporaryDirectory() as tmp:
            patches, state_file = self._patches(tmp)
            patches = (
                patch.object(pcs.intraday, "INTRADAY_STATE_FILE", state_file),
                patch("backend.services.trading_calendar.is_trading_day", return_value=False),
            )
            for p in patches:
                p.start()
            try:
                result = pcs.finalize_principal_capital_session(
                    "am", now=NOW, execution_mode="official", owner_id=OWNER)
            finally:
                for p in patches:
                    p.stop()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], its.SummaryReason.NON_TRADING_DAY)

    def test_reconcile_recovers_missed_scheduler(self):
        # 故障注入 #3：11:35 scheduler 完全没执行 → 12:00 reconciler 自动补 AM 并完成
        with TemporaryDirectory() as tmp:
            patches, state_file = self._patches(tmp)
            _write_state(state_file)
            for p in patches:
                p.start()
            try:
                with patch.object(pcs, "send_email", return_value=(True, None)):
                    result = pcs.reconcile_summary_jobs(
                        now=datetime(2026, 9, 15, 12, 0, tzinfo=BEIJING_TZ),
                        execution_mode="official")
                state = its.load_state_raw(path=state_file)
            finally:
                for p in patches:
                    p.stop()
        self.assertIn("am", result["processed"])
        self.assertEqual(its.get_summary_job(state, "am")["status"], "completed")

    def test_reconcile_retries_after_snapshot_arrives(self):
        # 故障注入 #2：NO_VALID_SNAPSHOT → retry_wait；snapshot 到达 + 越过 next_retry → 完成
        with TemporaryDirectory() as tmp:
            patches, state_file = self._patches(tmp)
            state = its.empty_state("2026-09-15")
            its.save_state(state, path=state_file)
            for p in patches:
                p.start()
            try:
                first = pcs.finalize_principal_capital_session(
                    "am", now=NOW, execution_mode="official", owner_id=OWNER)
                self.assertEqual(first["status"], "retry_wait")
                self.assertEqual(first["reason"], its.SummaryReason.NO_VALID_SNAPSHOT)
                # snapshot 稍后到达
                st = its.load_state_raw(path=state_file)
                st["session_snapshots"] = {"am": _snapshot(session="am", batch_id="b1")}
                its.save_state(st, path=state_file)
                with patch.object(pcs, "send_email", return_value=(True, None)):
                    result = pcs.reconcile_summary_jobs(
                        now=NOW + timedelta(minutes=2), execution_mode="official")
                state = its.load_state_raw(path=state_file)
            finally:
                for p in patches:
                    p.stop()
        self.assertIn("am", result["processed"])
        self.assertEqual(its.get_summary_job(state, "am")["status"], "completed")

    def test_repair_blocks_non_current_trade_date(self):
        # P1-6：repair execute 对非当前 state_trade_date 必须报错（禁止误改历史）
        with TemporaryDirectory() as tmp:
            patches, state_file = self._patches(tmp)
            _write_state(state_file)  # trade_date = 2026-09-15
            for p in patches:
                p.start()
            try:
                with self.assertRaises(RuntimeError):
                    pcs.summary_repair("2026-09-20", "am", dry_run=False, execute=True)
            finally:
                for p in patches:
                    p.stop()


class SummaryStateMachineTest(unittest.TestCase):
    """§8/§11/§12/§13 状态机纯函数 + 幂等 + 可恢复 + stale worker 防护测试。"""

    def test_transitions_not_attempted_to_completed(self):
        state = its.empty_state("2026-09-15")
        ok, state, _ = its.dispatch_summary_job(state, "am", "b1", NOW, "scheduler")
        self.assertTrue(ok)
        self.assertEqual(its.get_summary_job(state, "am")["status"], "queued")
        ok, state, _ = its.start_summary_job(state, "am", "w1", NOW)
        self.assertTrue(ok)
        self.assertEqual(its.get_summary_job(state, "am")["status"], "running")
        run_id = its.get_summary_job(state, "am")["run_id"]
        state = its.complete_summary_job(state, "am", NOW, run_id=run_id)
        self.assertEqual(its.get_summary_job(state, "am")["status"], "completed")
        # transition history 精确记录 from→to（评审 #7）
        history = [(h["from"], h["to"]) for h in its.get_summary_job(state, "am")["history"]]
        self.assertEqual(history, [
            ("not_attempted", "queued"),
            ("queued", "running"),
            ("running", "completed"),
        ])

    def test_dispatch_idempotent(self):
        state = its.empty_state("2026-09-15")
        ok1, state, _ = its.dispatch_summary_job(state, "am", "b1", NOW, "scheduler")
        self.assertTrue(ok1)
        ok2, _, reason = its.dispatch_summary_job(state, "am", "b1", NOW, "scheduler")
        self.assertFalse(ok2)
        self.assertEqual(reason, its.SummaryReason.ALREADY_RUNNING)

    def test_start_only_from_queued(self):
        state = its.empty_state("2026-09-15")
        ok, _, reason = its.start_summary_job(state, "am", "w1", NOW)
        self.assertFalse(ok)
        self.assertEqual(reason, its.SummaryReason.ALREADY_RUNNING)

    def test_retry_wait_within_max_attempts(self):
        state = its.empty_state("2026-09-15")
        ok, state, _ = its.dispatch_summary_job(state, "am", "b1", NOW, "scheduler")
        ok, state, _ = its.start_summary_job(state, "am", "w1", NOW)
        run_id = its.get_summary_job(state, "am")["run_id"]
        state = its.fail_summary_job(state, "am", "SEND_FAILED", "boom", NOW, run_id=run_id)
        self.assertEqual(its.get_summary_job(state, "am")["status"], "retry_wait")
        # 重试未到 next_retry_at → 拒绝 dispatch
        ok, _, reason = its.dispatch_summary_job(state, "am", "b1", NOW, "retry")
        self.assertFalse(ok)
        self.assertEqual(reason, its.SummaryReason.NOT_DUE)
        # force 可越过 next_retry_at（manual repair）
        ok, state, _ = its.dispatch_summary_job(state, "am", "b1", NOW, "manual_repair", force=True)
        self.assertTrue(ok)
        self.assertEqual(its.get_summary_job(state, "am")["status"], "queued")

    def test_dead_requires_manual_repair(self):
        # 3 次实际发送失败后 → dead；自动 dispatch 拒绝，manual repair(force) 重置并重进队列
        state = its.empty_state("2026-09-15")
        ok, state, _ = its.dispatch_summary_job(state, "am", "b1", NOW, "scheduler")
        ok, state, _ = its.start_summary_job(state, "am", "w1", NOW)
        for i in range(3):
            run_id = its.get_summary_job(state, "am")["run_id"]
            # 模拟进入发送阶段（attempt_count 仅在真正发送时消耗）
            state = its.mark_delivery_sending(state, "am", "summary:2026-09-15:AM", f"att-{i}", NOW, run_id=run_id)
            state = its.fail_summary_job(state, "am", "SEND_FAILED", "boom", NOW, next_retry_at=None, run_id=run_id)
            if its.get_summary_job(state, "am")["status"] == "dead":
                break
            ok, state, _ = its.dispatch_summary_job(state, "am", "b1", NOW, "retry", force=True)
            ok, state, _ = its.start_summary_job(state, "am", "w1", NOW)
        self.assertEqual(its.get_summary_job(state, "am")["status"], "dead")
        # 自动 dispatch（无 force）→ MAX_RETRIES_EXCEEDED
        ok, _, reason = its.dispatch_summary_job(state, "am", "b1", NOW, "retry")
        self.assertFalse(ok)
        self.assertEqual(reason, its.SummaryReason.MAX_RETRIES_EXCEEDED)
        # manual repair（force）→ 重置计数，重新 queued
        ok, state, _ = its.dispatch_summary_job(state, "am", "b1", NOW, "manual_repair", force=True)
        self.assertTrue(ok)
        self.assertEqual(its.get_summary_job(state, "am")["status"], "queued")
        self.assertEqual(its.get_summary_job(state, "am")["attempt_count"], 0)

    def test_stale_running_recovery(self):
        state = its.empty_state("2026-09-15")
        ok, state, _ = its.dispatch_summary_job(state, "am", "b1", NOW, "scheduler")
        ok, state, _ = its.start_summary_job(state, "am", "w1", NOW)
        self.assertEqual(its.get_summary_job(state, "am")["status"], "running")
        state2, recovered = its.recover_stale_running(state, NOW + timedelta(minutes=11), timeout_seconds=600)
        self.assertEqual(recovered, ["am"])
        self.assertEqual(its.get_summary_job(state2, "am")["status"], "retry_wait")
        self.assertEqual(its.get_summary_job(state2, "am")["last_error_code"], "WORKER_STALE")

    def test_stale_worker_cannot_write(self):
        # P0：worker A stale 被 B 接管后，A 回魂用旧 run_id 写 completed 必须被拒绝
        state = its.empty_state("2026-09-15")
        ok, state, _ = its.dispatch_summary_job(state, "am", "b1", NOW, "scheduler")
        ok, state, _ = its.start_summary_job(state, "am", "worker-A", NOW)
        run_id_a = its.get_summary_job(state, "am")["run_id"]
        # A 被判 stale，B 接管
        state, recovered = its.recover_stale_running(state, NOW + timedelta(minutes=11), timeout_seconds=600)
        self.assertEqual(recovered, ["am"])
        ok, state, _ = its.dispatch_summary_job(state, "am", "b1", NOW + timedelta(minutes=12), "reconciler", force=True)
        ok, state, _ = its.start_summary_job(state, "am", "worker-B", NOW + timedelta(minutes=12))
        run_id_b = its.get_summary_job(state, "am")["run_id"]
        self.assertNotEqual(run_id_a, run_id_b)
        # A 回魂：旧 run_id complete → 拒绝，状态保持 running
        state2 = its.complete_summary_job(state, "am", NOW + timedelta(minutes=13), run_id=run_id_a)
        self.assertEqual(its.get_summary_job(state2, "am")["status"], "running")
        # B 用正确 run_id complete → 成功
        state3 = its.complete_summary_job(state, "am", NOW + timedelta(minutes=13), run_id=run_id_b)
        self.assertEqual(its.get_summary_job(state3, "am")["status"], "completed")

    def test_stale_worker_after_recovery_before_new_start(self):
        # 评审 #1：A stale → recovery 清空 run_id → B 还没 start → A 回魂 → 拒绝
        state = its.empty_state("2026-09-15")
        ok, state, _ = its.dispatch_summary_job(state, "am", "b1", NOW, "scheduler")
        ok, state, _ = its.start_summary_job(state, "am", "w1", NOW)
        run_id_a = its.get_summary_job(state, "am")["run_id"]
        state, recovered = its.recover_stale_running(state, NOW + timedelta(minutes=11), timeout_seconds=600)
        self.assertEqual(recovered, ["am"])
        self.assertIsNone(its.get_summary_job(state, "am")["run_id"])
        # A 回魂 complete（run_id 已被清空）→ 严格匹配拒绝
        state2 = its.complete_summary_job(state, "am", NOW + timedelta(minutes=12), run_id=run_id_a)
        self.assertEqual(its.get_summary_job(state2, "am")["status"], "retry_wait")

    def test_completed_cannot_fail_skip_delivery_unknown(self):
        # 评审 #2/#3/#4：终态 completed 不得被 fail/skip/delivery_unknown 回退
        state = its.empty_state("2026-09-15")
        ok, state, _ = its.dispatch_summary_job(state, "am", "b1", NOW, "scheduler")
        ok, state, _ = its.start_summary_job(state, "am", "w1", NOW)
        run_id = its.get_summary_job(state, "am")["run_id"]
        state = its.complete_summary_job(state, "am", NOW, run_id=run_id)
        self.assertEqual(its.get_summary_job(state, "am")["status"], "completed")
        s2 = its.fail_summary_job(state, "am", "SEND_FAILED", "x", NOW, run_id=run_id)
        self.assertEqual(its.get_summary_job(s2, "am")["status"], "completed")
        s3 = its.skip_summary_job(state, "am", "X", NOW, run_id=run_id)
        self.assertEqual(its.get_summary_job(s3, "am")["status"], "completed")
        s4 = its.mark_delivery_unknown_terminal(state, "am", "x", NOW, run_id=run_id)
        self.assertEqual(its.get_summary_job(s4, "am")["status"], "completed")

    def test_session_mismatch_is_broken(self):
        # 评审 #9：SESSION_MISMATCH 是数据完整性问题，health 应为 broken（非 healthy skipped）
        state = its.empty_state("2026-09-15")
        state = its.skip_summary_job(state, "am", its.SummaryReason.SESSION_MISMATCH, NOW)
        health = its.assess_summary_health(state, "am", None, NOW)
        self.assertEqual(health["health"], "broken")
        self.assertEqual(health["reason"], its.SummaryReason.SESSION_MISMATCH)

    def test_send_then_crash_no_resend(self):
        # 第二轮 #1：send 成功后、complete 前 crash（running + delivery=sending）
        # → 恢复为 delivery_unknown 终态，禁止自动重发
        state = its.empty_state("2026-09-15")
        ok, state, _ = its.dispatch_summary_job(state, "am", "b1", NOW, "scheduler")
        ok, state, _ = its.start_summary_job(state, "am", "w1", NOW)
        run_id = its.get_summary_job(state, "am")["run_id"]
        state = its.mark_delivery_sending(state, "am", "summary:2026-09-15:AM", "att-1", NOW, run_id=run_id)
        self.assertEqual(its.get_summary_job(state, "am")["delivery"]["status"], "sending")
        # 11 分钟后 reconciler 判定 stale → delivery_unknown 终态
        state2, recovered = its.recover_stale_running(state, NOW + timedelta(minutes=11), timeout_seconds=600)
        self.assertEqual(recovered, ["am"])
        self.assertEqual(its.get_summary_job(state2, "am")["status"], "delivery_unknown")
        self.assertEqual(its.get_summary_job(state2, "am")["delivery"]["status"], "unknown")
        # 自动 dispatch 被拒绝（禁止重发）
        ok, _, reason = its.dispatch_summary_job(state2, "am", "b1", NOW + timedelta(minutes=12), "reconciler")
        self.assertFalse(ok)
        self.assertEqual(reason, its.SummaryReason.DELIVERY_UNKNOWN)

    def test_skip_is_terminal(self):
        state = its.empty_state("2026-09-15")
        state = its.skip_summary_job(state, "am", its.SummaryReason.NON_TRADING_DAY, NOW)
        self.assertEqual(its.get_summary_job(state, "am")["status"], "skipped")
        ok, _, reason = its.dispatch_summary_job(state, "am", "b1", NOW, "scheduler")
        self.assertFalse(ok)
        self.assertEqual(reason, its.SummaryReason.NON_TRADING_DAY)

    def test_no_snapshot_does_not_consume_retry_budget(self):
        # P1-5：NO_VALID_SNAPSHOT 前置失败不消耗 attempt_count，多次重试不 DEAD
        state = its.empty_state("2026-09-15")
        for _ in range(5):
            ok, state, _ = its.dispatch_summary_job(state, "am", "b1", NOW, "scheduler", force=True)
            self.assertTrue(ok)
            ok, state, _ = its.start_summary_job(state, "am", "w1", NOW)
            run_id = its.get_summary_job(state, "am")["run_id"]
            state = its.fail_summary_job(state, "am", its.SummaryReason.NO_VALID_SNAPSHOT, "no snapshot", NOW, run_id=run_id)
            self.assertEqual(its.get_summary_job(state, "am")["status"], "retry_wait")
            self.assertEqual(its.get_summary_job(state, "am")["attempt_count"], 0)
        self.assertNotEqual(its.get_summary_job(state, "am")["status"], "dead")

    def test_prerequisite_retries_bounded(self):
        # NO_VALID_SNAPSHOT 前置重试有上限（默认 6），超过后 skipped 终态，避免无限循环
        state = its.empty_state("2026-09-15")
        for _ in range(6):
            ok, state, _ = its.dispatch_summary_job(state, "am", "b1", NOW, "scheduler", force=True)
            ok, state, _ = its.start_summary_job(state, "am", "w1", NOW)
            run_id = its.get_summary_job(state, "am")["run_id"]
            state = its.fail_summary_job(state, "am", its.SummaryReason.NO_VALID_SNAPSHOT, "no snapshot", NOW, run_id=run_id)
        self.assertEqual(its.get_summary_job(state, "am")["status"], "skipped")
        self.assertEqual(its.get_summary_job(state, "am")["skip_reason"], its.SummaryReason.NO_VALID_SNAPSHOT)

    def test_retryable_reason_goes_retry_wait_not_skipped(self):
        # P0：NO_VALID_SNAPSHOT / SNAPSHOT_NOT_READY 属于可恢复，不落 skipped 终态
        self.assertTrue(its.is_retryable(its.SummaryReason.NO_VALID_SNAPSHOT))
        self.assertTrue(its.is_retryable(its.SummaryReason.SNAPSHOT_NOT_READY))
        self.assertTrue(its.is_retryable(its.SummaryReason.WORKER_STALE))
        self.assertFalse(its.is_retryable(its.SummaryReason.NOTIFY_NOT_ELIGIBLE))
        self.assertTrue(its.is_terminal_skip(its.SummaryReason.NOTIFY_NOT_ELIGIBLE))
        self.assertTrue(its.is_terminal_skip(its.SummaryReason.SESSION_MISMATCH))
        self.assertFalse(its.is_terminal_skip(its.SummaryReason.NO_VALID_SNAPSHOT))

    def test_idempotency_key(self):
        self.assertEqual(its.summary_job_idempotency_key("2026-09-28", "am"), "summary:2026-09-28:AM")
        self.assertEqual(its.summary_job_idempotency_key("2026-09-28", "pm"), "summary:2026-09-28:PM")

    def test_old_minimal_summary_state_migrated(self):
        # 旧状态文件的 minimal summary_state（仅 status）应被迁移补齐全部字段
        state = its.empty_state("2026-09-15")
        state["summary_state"] = {"am": {"status": "not_attempted"}, "pm": {"status": "not_attempted"}}
        reset = its.reset_state_for_trade_date(state, "2026-09-15")
        am = reset["summary_state"]["am"]
        self.assertEqual(am["attempt_count"], 0)
        self.assertEqual(am["dispatch_count"], 0)
        self.assertIsInstance(am["history"], list)
        self.assertIsInstance(am["delivery"], dict)
        self.assertEqual(am["status"], "not_attempted")

    def test_timezone_independent(self):
        # §32：due/cutoff 固定北京时间，不受服务器本地时区影响
        due_am = its.finalizer_due_at("2026-09-28", "am")
        due_pm = its.finalizer_due_at("2026-09-28", "pm")
        self.assertEqual(due_am.hour, 11)
        self.assertEqual(due_am.minute, 35)
        self.assertEqual(due_am.utcoffset(), timedelta(hours=8))
        self.assertEqual(due_pm.hour, 15)
        self.assertEqual(due_pm.minute, 5)
        self.assertEqual(due_pm.utcoffset(), timedelta(hours=8))

    def test_two_reconcilers_only_one_dispatches(self):
        # 第二轮 #4：两个 reconciler 并发 dispatch，文件锁 CAS 保证只有一个成功
        with TemporaryDirectory() as tmp:
            sf = Path(tmp) / "state.json"
            its.save_state(its.empty_state("2026-09-15"), path=sf)
            with patch.object(its, "INTRADAY_STATE_FILE", sf):
                ok1, _, _ = its.atomic_dispatch_summary_job(its.INTRADAY_STATE_FILE, "am", "b1", NOW, "reconciler")
                ok2, _, reason = its.atomic_dispatch_summary_job(its.INTRADAY_STATE_FILE, "am", "b1", NOW, "reconciler")
        self.assertTrue(ok1)
        self.assertFalse(ok2)
        self.assertEqual(reason, its.SummaryReason.ALREADY_RUNNING)

    def test_completed_cannot_regress(self):
        # 第二轮 #8：repair 与 reconciler 都不允许把 completed 状态回退
        state = its.empty_state("2026-09-15")
        ok, state, _ = its.dispatch_summary_job(state, "am", "b1", NOW, "scheduler")
        ok, state, _ = its.start_summary_job(state, "am", "w1", NOW)
        run_id = its.get_summary_job(state, "am")["run_id"]
        state = its.complete_summary_job(state, "am", NOW, run_id=run_id)
        self.assertEqual(its.get_summary_job(state, "am")["status"], "completed")
        # 任何后续 dispatch/start 都不得回退
        ok, _, reason = its.dispatch_summary_job(state, "am", "b1", NOW, "reconciler", force=True)
        self.assertFalse(ok)
        self.assertEqual(reason, its.SummaryReason.ALREADY_COMPLETED)


if __name__ == "__main__":
    unittest.main()
