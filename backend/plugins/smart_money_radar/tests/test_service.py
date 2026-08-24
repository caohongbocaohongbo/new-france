import json
import asyncio
import time

from backend.plugins.smart_money_radar import service
from backend.plugins.smart_money_radar.store import RadarStore


def write_pool(path, items):
    path.write_text(
        json.dumps(
            {
                "buy_triggered": items,
                "sell_triggered": [],
                "buy_candidates": len(items),
                "sell_candidates": 0,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_load_watch_pool_filters_non_main_board_and_limits(tmp_path, monkeypatch):
    pool_file = tmp_path / "principal_capital_latest.json"
    write_pool(
        pool_file,
        [
            {"code": "600001", "name": "主板A", "main_inflow_ratio": 61, "total_amount": 2e8},
            {"code": "300001", "name": "创业板", "main_inflow_ratio": 70, "total_amount": 2e8},
            {"code": "688001", "name": "科创板", "main_inflow_ratio": 70, "total_amount": 2e8},
            {"code": "600002", "name": "ST风险", "main_inflow_ratio": 70, "total_amount": 2e8},
        ],
    )
    cfg = dict(service.CONFIG)
    cfg.update({"pool_source_file": str(pool_file), "pool_max": 1})
    monkeypatch.setattr(service, "CONFIG", cfg)

    result = service.load_watch_pool(force=True)

    assert [item["code"] for item in result] == ["600001"]


def test_run_radar_once_empty_pool_writes_latest_without_email(tmp_path, monkeypatch, fixed_now):
    latest_file = tmp_path / "latest.json"
    cfg = dict(service.CONFIG)
    cfg.update({"pool_source_file": str(tmp_path / "missing.json")})
    monkeypatch.setattr(service, "CONFIG", cfg)
    monkeypatch.setattr(service, "LATEST_FILE", latest_file)
    monkeypatch.setattr(service, "build_and_send", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no email")))

    result = asyncio.run(service.run_radar_once(now=fixed_now, dry_run=False, force=True, store=RadarStore()))

    assert result["status"] == "empty_pool"
    assert latest_file.exists()
    assert json.loads(latest_file.read_text(encoding="utf-8"))["hits"] == []


def test_run_radar_once_hits_strength_signal_and_respects_dry_run(
    tmp_path, monkeypatch, fixed_now, fake_stock_payload
):
    latest_file = tmp_path / "latest.json"
    state_dir = tmp_path / "data"
    pool_file = tmp_path / "principal_capital_latest.json"
    write_pool(
        pool_file,
        [{"code": "600001", "name": "主板A", "main_inflow_ratio": 66, "main_net_inflow": 5e7, "total_amount": 2e8}],
    )
    cfg = dict(service.CONFIG)
    cfg.update(
        {
            "pool_source_file": str(pool_file),
            "strength_threshold": 60,
            "active_buy_threshold": 0.55,
            "alert_cooldown_minutes": 30,
        }
    )
    monkeypatch.setattr(service, "CONFIG", cfg)
    monkeypatch.setattr(service, "LATEST_FILE", latest_file)
    monkeypatch.setattr(service, "NOTIFIED_DIR", state_dir)
    monkeypatch.setattr(service, "poll_pool_once", lambda pool, watch_pool, cfg: [fake_stock_payload])
    monkeypatch.setattr(service, "build_and_send", lambda *a, **k: (_ for _ in ()).throw(AssertionError("dry run")))
    monkeypatch.setattr(service, "transition_stage", lambda previous, metrics: {"stage": "吸筹确认", "previous_stage": "潜伏", "changed": True})

    result = asyncio.run(service.run_radar_once(now=fixed_now, dry_run=True, force=True, store=RadarStore()))

    assert result["status"] == "completed"
    assert result["hit_count"] == 1
    assert result["hits"][0]["stage"] == "吸筹确认"
    payload = json.loads(latest_file.read_text(encoding="utf-8"))
    assert payload["hits"][0]["code"] == "600001"


def test_run_radar_once_dry_run_does_not_persist_notification_cooldown(
    tmp_path, monkeypatch, fixed_now, fake_stock_payload
):
    state_dir = tmp_path / "data"
    pool_file = tmp_path / "principal_capital_latest.json"
    write_pool(
        pool_file,
        [{"code": "600001", "name": "主板A", "main_inflow_ratio": 66, "main_net_inflow": 5e7}],
    )
    cfg = dict(service.CONFIG)
    cfg.update({"pool_source_file": str(pool_file), "strength_threshold": 60})
    monkeypatch.setattr(service, "CONFIG", cfg)
    monkeypatch.setattr(service, "NOTIFIED_DIR", state_dir)
    monkeypatch.setattr(service, "poll_pool_once", lambda pool, watch_pool, cfg: [fake_stock_payload])
    monkeypatch.setattr(service, "transition_stage", lambda previous, metrics: {"stage": "吸筹确认", "previous_stage": "潜伏", "changed": True})

    result = asyncio.run(service.run_radar_once(now=fixed_now, dry_run=True, force=True, store=RadarStore()))

    assert result["hit_count"] == 1
    assert not list(state_dir.glob("smart_money_radar_notified_*.json"))


def test_load_watch_pool_adapts_existing_count_fields_to_trigger_lists(tmp_path, monkeypatch):
    pool_file = tmp_path / "principal_capital_latest.json"
    pool_file.write_text(
        json.dumps(
            {
                "buy_candidates": 1,
                "sell_candidates": 0,
                "buy_triggered": [
                    {"code": "600001", "name": "主板A", "main_inflow_ratio": 66}
                ],
                "sell_triggered": [],
            }
        ),
        encoding="utf-8",
    )
    cfg = dict(service.CONFIG)
    cfg.update({"pool_source_file": str(pool_file), "pool_max": 40})
    monkeypatch.setattr(service, "CONFIG", cfg)

    result = service.load_watch_pool(force=True)

    assert [item["code"] for item in result] == ["600001"]


def test_run_radar_once_collects_phase2_data_and_uses_cache(
    tmp_path, monkeypatch, fixed_now, fake_stock_payload
):
    class FakePool:
        def __init__(self):
            self.bars_calls = []
            self.finance_calls = 0

        def fetch_bars(self, market, code, category, n):
            self.bars_calls.append((market, code, category, n))
            return [
                {"close": 10, "high": 10.5, "low": 9.8, "vol": 100, "amount": 100000},
                {"close": 10.2, "high": 10.4, "low": 10, "vol": 120, "amount": 120000},
            ]

        def fetch_finance(self, market, code):
            self.finance_calls += 1
            return {"liutongguben": 1000000}

    latest_file = tmp_path / "latest.json"
    pool_file = tmp_path / "principal_capital_latest.json"
    write_pool(pool_file, [{"code": "600001", "name": "主板A", "main_inflow_ratio": 66, "main_net_inflow": 5e7}])
    cfg = dict(service.CONFIG)
    cfg.update({"pool_source_file": str(pool_file), "strength_threshold": 60})
    monkeypatch.setattr(service, "CONFIG", cfg)
    monkeypatch.setattr(service, "LATEST_FILE", latest_file)
    monkeypatch.setattr(service, "poll_pool_once", lambda pool, watch_pool, cfg: [fake_stock_payload])
    monkeypatch.setattr(service, "transition_stage", lambda previous, metrics: {"stage": "吸筹确认", "previous_stage": "潜伏", "changed": True})
    fake_pool = FakePool()
    store = RadarStore()

    first = asyncio.run(service.run_radar_once(now=fixed_now, dry_run=True, force=True, store=store, pool=fake_pool))
    second = asyncio.run(service.run_radar_once(now=fixed_now, dry_run=True, force=True, store=store, pool=fake_pool))

    assert first["hits"][0]["return_1m"] == 0.02
    assert first["hits"][0]["price_impact"] is not None
    assert second["hits"][0]["vwap_deviation"] == first["hits"][0]["vwap_deviation"]
    assert len(fake_pool.bars_calls) == 3
    assert fake_pool.finance_calls == 1


def test_run_radar_once_clips_negative_price_impact_before_scoring(
    tmp_path, monkeypatch, fixed_now, fake_stock_payload
):
    pool_file = tmp_path / "principal_capital_latest.json"
    write_pool(pool_file, [{"code": "600001", "name": "主板A", "main_inflow_ratio": 66, "main_net_inflow": 5e7}])
    cfg = dict(service.CONFIG)
    cfg.update({"pool_source_file": str(pool_file), "strength_threshold": 60})
    monkeypatch.setattr(service, "CONFIG", cfg)
    monkeypatch.setattr(service, "LATEST_FILE", tmp_path / "latest.json")
    monkeypatch.setattr(service, "poll_pool_once", lambda pool, watch_pool, cfg: [fake_stock_payload])
    monkeypatch.setattr(service, "price_impact", lambda *args: -2.0)
    monkeypatch.setattr(
        service,
        "transition_stage",
        lambda previous, metrics: {"stage": "吸筹确认", "previous_stage": "潜伏", "changed": True},
    )

    result = asyncio.run(
        service.run_radar_once(
            now=fixed_now,
            dry_run=True,
            force=True,
            store=RadarStore(),
            pool=object(),
        )
    )

    assert result["hits"][0]["price_impact"] == 0.0


def test_run_radar_once_isolates_phase2_fetch_failure(tmp_path, monkeypatch, fixed_now, fake_stock_payload):
    class BrokenPool:
        def fetch_bars(self, *args):
            raise RuntimeError("bars unavailable")

        def fetch_finance(self, *args):
            raise RuntimeError("finance unavailable")

    pool_file = tmp_path / "principal_capital_latest.json"
    write_pool(pool_file, [{"code": "600001", "name": "主板A", "main_inflow_ratio": 66, "main_net_inflow": 5e7}])
    cfg = dict(service.CONFIG)
    cfg.update({"pool_source_file": str(pool_file), "strength_threshold": 60})
    monkeypatch.setattr(service, "CONFIG", cfg)
    monkeypatch.setattr(service, "LATEST_FILE", tmp_path / "latest.json")
    monkeypatch.setattr(service, "poll_pool_once", lambda pool, watch_pool, cfg: [fake_stock_payload])

    result = asyncio.run(service.run_radar_once(now=fixed_now, dry_run=True, force=True, store=RadarStore(), pool=BrokenPool()))

    assert result["status"] == "completed"
    assert any("bars unavailable" in error for error in result["errors"])


def test_run_radar_once_times_out_slow_phase2_fetch(tmp_path, monkeypatch, fixed_now, fake_stock_payload):
    class SlowPool:
        def fetch_bars(self, *args):
            time.sleep(0.05)
            return []

        def fetch_finance(self, *args):
            return {}

    pool_file = tmp_path / "principal_capital_latest.json"
    write_pool(pool_file, [{"code": "600001", "name": "主板A", "main_inflow_ratio": 66, "main_net_inflow": 5e7}])
    cfg = dict(service.CONFIG)
    cfg.update({"pool_source_file": str(pool_file), "tdx_socket_timeout_s": 0.01})
    monkeypatch.setattr(service, "CONFIG", cfg)
    monkeypatch.setattr(service, "LATEST_FILE", tmp_path / "latest.json")
    monkeypatch.setattr(service, "poll_pool_once", lambda pool, watch_pool, cfg: [fake_stock_payload])

    result = asyncio.run(service.run_radar_once(now=fixed_now, dry_run=True, force=True, store=RadarStore(), pool=SlowPool()))

    assert any("超时" in error for error in result["errors"])


def test_current_day_bars_excludes_previous_trading_day(fixed_now):
    bars = [
        {"datetime": "2026-08-17 14:59", "close": 20},
        {"datetime": "2026-08-18 09:31", "close": 10},
    ]
    assert service._current_day_bars(bars, fixed_now) == [bars[1]]


def test_history_volume_groups_previous_days_by_minute(fixed_now):
    bars = [
        {"datetime": "2026-08-15 09:31", "vol": 100},
        {"datetime": "2026-08-17 09:31", "vol": 120},
        {"datetime": "2026-08-18 09:31", "vol": 999},
        {"datetime": "2026-08-17 14:59", "vol": 1e-30},
    ]
    assert service._history_volume_by_minute(bars, fixed_now) == {"09:31": [100.0, 120.0]}


def test_run_radar_once_emits_phase3_metrics_and_stage_state(
    tmp_path, monkeypatch, fixed_now, fake_stock_payload
):
    pool_file = tmp_path / "principal_capital_latest.json"
    write_pool(pool_file, [{"code": "600001", "name": "主板A", "main_inflow_ratio": 66, "main_net_inflow": 5e7}])
    cfg = dict(service.CONFIG)
    cfg.update({"pool_source_file": str(pool_file), "strength_threshold": 60, "latent_rounds": 1, "latent_volume_ratio": 0})
    monkeypatch.setattr(service, "CONFIG", cfg)
    monkeypatch.setattr(service, "LATEST_FILE", tmp_path / "latest.json")
    monkeypatch.setattr(service, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(service, "NOTIFIED_DIR", tmp_path / "notified")
    monkeypatch.setattr(service, "poll_pool_once", lambda pool, watch_pool, cfg: [fake_stock_payload])
    monkeypatch.setattr(service, "transition_stage", lambda previous, metrics: {"stage": "吸筹确认", "previous_stage": "潜伏", "changed": True})

    result = asyncio.run(service.run_radar_once(now=fixed_now, dry_run=True, force=True, store=RadarStore(), pool=object()))

    assert result["hit_count"] == 1
    hit = result["hits"][0]
    assert "smart_money_score" in hit
    assert "launch_score" in hit
    assert "decay_score" in hit
    assert hit["stage"] == "吸筹确认"
    for key in (
        "large_buy_amount", "large_sell_amount", "main_net_inflow", "main_inflow_ratio",
        "fund_persistence", "return_1m", "return_5m", "return_15m", "vwap_deviation",
        "distance_from_high", "price_impact", "volume_ratio", "decay_score",
    ):
        assert key in hit
