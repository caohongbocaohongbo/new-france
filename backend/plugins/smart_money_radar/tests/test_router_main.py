from backend import main
from backend.plugins.smart_money_radar import router


def test_fastapi_registers_smart_money_radar_routes():
    app = main.get_app()
    paths = {route.path for route in app.routes}

    assert "/api/v1/smart-money-radar/latest" in paths
    assert "/api/v1/smart-money-radar/pool" in paths
    assert "/api/v1/smart-money-radar/trigger" in paths


def test_latest_endpoint_returns_stable_error_when_reader_fails(monkeypatch):
    monkeypatch.setattr(router, "read_latest", lambda: (_ for _ in ()).throw(OSError("broken latest")))

    import asyncio
    result = asyncio.run(router.latest_smart_money_radar())

    assert result["status"] == "error"
    assert "broken latest" in result["message"]


def test_pool_endpoint_returns_stable_error_when_reader_fails(monkeypatch):
    monkeypatch.setattr(router, "load_watch_pool", lambda force: (_ for _ in ()).throw(OSError("broken pool")))

    import asyncio
    result = asyncio.run(router.smart_money_radar_pool())

    assert result["status"] == "error"
    assert "broken pool" in result["message"]


def test_trigger_endpoint_returns_stable_error_when_pipeline_fails(monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError("broken pipeline")

    monkeypatch.setattr(router, "run_radar_once", fail)

    import asyncio
    result = asyncio.run(router.trigger_smart_money_radar(dry_run=True, force=False))

    assert result["status"] == "error"
    assert "broken pipeline" in result["message"]
