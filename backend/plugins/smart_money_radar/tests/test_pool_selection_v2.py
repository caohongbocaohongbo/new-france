"""23 v2 雷达池只消费 buy_candidates_current + 日内状态池选择结果。"""
from datetime import datetime, timedelta, timezone

from backend.plugins.smart_money_radar import service as radar

BEIJING_TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 15, 14, 35, tzinfo=BEIJING_TZ)


def test_candidate_lists_prefers_buy_current_and_excludes_sell():
    payload = {
        "buy_candidates_current": [{"code": "600001", "main_inflow_ratio": 60}],
        "sell_candidates_current": [{"code": "600002", "main_inflow_ratio": -35}],
        "buy_triggered": [{"code": "600003", "main_inflow_ratio": 70}],
    }
    items = radar._candidate_lists(payload)
    assert [item["code"] for item in items] == ["600001"]


def test_candidate_lists_falls_back_to_triggered():
    payload = {"buy_triggered": [{"code": "600003", "main_inflow_ratio": 70}], "sell_triggered": []}
    items = radar._candidate_lists(payload)
    assert [item["code"] for item in items] == ["600003"]


def test_pool_from_intraday_state_attaches_metrics(monkeypatch):
    state = {"pool_entries": {"600001": {
        "code": "600001", "latest_metrics": {"main_inflow_ratio": 66, "main_net_inflow": 6e7, "name": "A"},
    }}}
    monkeypatch.setattr("backend.plugins.principal_capital.intraday_state.load_state", lambda **kwargs: state)
    items = radar._pool_from_intraday_state(NOW)
    assert items[0]["code"] == "600001"
    assert items[0]["main_inflow_ratio"] == 66


def test_load_watch_pool_uses_state_pool(monkeypatch):
    monkeypatch.setattr(
        radar, "_pool_from_intraday_state",
        lambda now: [{"code": "600001", "main_inflow_ratio": 60, "total_amount": 2e8}],
    )
    radar._POOL_CACHE.clear()
    items = radar.load_watch_pool(force=True, now=NOW)
    assert [item["code"] for item in items] == ["600001"]


def test_load_watch_pool_respects_authoritative_empty(monkeypatch, tmp_path):
    # P1-5 / A13：权威空池 [] 不得回退旧报告复活陈旧候选
    pool_file = tmp_path / "principal_capital_latest.json"
    pool_file.write_text(
        '{"status": "completed", "buy_triggered": [{"code": "600001", "name": "A", "main_inflow_ratio": 61, "total_amount": 200000000}], "sell_triggered": []}',
        encoding="utf-8",
    )
    cfg = dict(radar.CONFIG)
    cfg.update({"pool_source_file": str(pool_file), "pool_max": 10})
    monkeypatch.setattr(radar, "CONFIG", cfg)
    monkeypatch.setattr(radar, "_pool_from_intraday_state", lambda now: [])
    radar._POOL_CACHE.clear()
    items = radar.load_watch_pool(force=True, now=NOW)
    assert items == []


def test_load_watch_pool_falls_back_when_state_unavailable(monkeypatch, tmp_path):
    pool_file = tmp_path / "principal_capital_latest.json"
    pool_file.write_text(
        '{"status": "completed", "buy_triggered": [{"code": "600001", "name": "A", "main_inflow_ratio": 61, "total_amount": 200000000}], "sell_triggered": []}',
        encoding="utf-8",
    )
    cfg = dict(radar.CONFIG)
    cfg.update({"pool_source_file": str(pool_file), "pool_max": 10})
    monkeypatch.setattr(radar, "CONFIG", cfg)
    monkeypatch.setattr(radar, "_pool_from_intraday_state", lambda now: None)
    monkeypatch.setattr(radar, "_fetch_snapshot_json", lambda *a, **k: {})
    radar._POOL_CACHE.clear()
    items = radar.load_watch_pool(force=True, now=NOW)
    assert [item["code"] for item in items] == ["600001"]
