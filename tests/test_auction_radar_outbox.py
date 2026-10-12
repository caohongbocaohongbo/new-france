"""28 Outbox/DB 故障注入与状态流转测试。"""
import pandas as pd
import pytest

from backend.plugins.auction_radar import outbox, service


def test_update_run_checked_rejects_zero_rowcount(monkeypatch):
    monkeypatch.setattr(service, "update_run", lambda rid, **f: 0)
    with pytest.raises(RuntimeError):
        service.update_run_checked(1, state="PUBLISHED")


def test_recover_sending_resets_stale(monkeypatch):
    rows = pd.DataFrame([{"id": 7}, {"id": 8}])
    monkeypatch.setattr(outbox, "db_query", lambda sql, params=None: rows)
    updated = []
    monkeypatch.setattr(outbox, "update_outbox", lambda task_id, **f: updated.append((task_id, f)) or 1)
    n = outbox.recover_sending(timeout_s=30)
    assert n == 2
    assert len(updated) == 2


def test_dispatch_outbox_accept_and_retry(monkeypatch):
    tasks = [
        {"id": 1, "payload_json": '{"subject":"s","text":"t","html":"h"}', "attempts": 0},
        {"id": 2, "payload_json": '{"subject":"s","text":"t","html":"h"}', "attempts": 0},
    ]
    monkeypatch.setattr(outbox, "db_query", lambda sql, params=None: pd.DataFrame())
    monkeypatch.setattr(outbox, "claim_ready", lambda limit=10: tasks)
    states = {}
    def fake_mark(task_id, **f):
        states[task_id] = f
        return 1
    monkeypatch.setattr(outbox, "mark_sending", lambda tid: fake_mark(tid, status="SENDING"))
    monkeypatch.setattr(outbox, "mark_accepted", lambda tid: fake_mark(tid, status="ACCEPTED"))
    monkeypatch.setattr(outbox, "mark_retry", lambda tid, e, a: fake_mark(tid, status="RETRY_WAIT", attempts=a))
    monkeypatch.setattr(outbox, "mark_failed_final", lambda tid, e: fake_mark(tid, status="FAILED_FINAL"))
    results = [ (True, None), (False, "smtp down") ]
    monkeypatch.setattr(outbox, "push_email", lambda subject, text, html, timeout=15: results.pop(0))

    r = outbox.dispatch_outbox()
    assert r["accepted"] == 1 and r["failed"] == 1
    assert states[1]["status"] == "ACCEPTED"
    assert states[2]["status"] == "RETRY_WAIT"


def test_dispatch_outbox_retry_exhausted(monkeypatch):
    tasks = [{"id": 3, "payload_json": '{"subject":"s"}', "attempts": 2}]
    monkeypatch.setattr(outbox, "db_query", lambda sql, params=None: pd.DataFrame())
    monkeypatch.setattr(outbox, "claim_ready", lambda limit=10: tasks)
    states = {}
    monkeypatch.setattr(outbox, "mark_failed_final", lambda tid, e: states.setdefault(tid, {"status": "FAILED_FINAL"}) or 1)
    monkeypatch.setattr(outbox, "mark_sending", lambda tid: 1)
    monkeypatch.setattr(outbox, "mark_accepted", lambda tid: 1)
    monkeypatch.setattr(outbox, "mark_retry", lambda tid, e, a: 1)
    monkeypatch.setattr(outbox, "push_email", lambda *a, **k: (True, None))
    monkeypatch.setattr(outbox, "CONFIG", {"outbox_retry_max": 2})
    r = outbox.dispatch_outbox()
    assert r["failed"] == 1
    assert states[3]["status"] == "FAILED_FINAL"
