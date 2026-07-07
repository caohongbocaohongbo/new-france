import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from backend.services import screening_service as svc


def test_run_full_pipeline_reads_snapshots_for_index_zt_pool_and_quotes():
    index_snapshot = {"value": 3300.0, "gain_pct": 0.5, "source": "cache"}
    zt_pool = pd.DataFrame([{"代码": "600000", "名称": "浦发银行", "最新价": 10.0, "涨跌幅": 9.9, "换手率": 3.0, "流通市值": 100.0, "封板时间": 93000, "炸板次数": 0, "连板数": 1}])
    quotes = pd.DataFrame([{"代码": "600000", "名称": "浦发银行", "最新价": 10.1, "换手率": 2.5, "量比": 1.2, "市盈率": 8.0}])

    async def fake_quotes(_codes):
        return pd.DataFrame()

    async def fake_hist(_codes):
        return {}

    async def fake_events(_target):
        return []

    with patch.object(svc, "_read_watchlist", return_value=[]), \
         patch.object(svc, "_fetch_principal_capital_map", return_value={}), \
         patch.object(svc, "_read_index_snapshot_with_cache", return_value=(index_snapshot, {"source": "cache"})) as read_index, \
         patch.object(svc, "_read_zt_pool_with_cache", return_value=(zt_pool, {"source": "cache"})) as read_zt, \
         patch.object(svc, "_read_quotes_with_cache", return_value=(quotes, {"source": "cache"})) as read_quotes, \
         patch.object(svc, "DataCollectorAgent") as collector_cls, \
         patch.object(svc, "EventEngine") as event_engine_cls:
        collector = collector_cls.return_value
        collector.collect_watchlist_quotes.side_effect = fake_quotes
        collector.collect_historical_batch.side_effect = fake_hist
        event_engine_cls.return_value.collect_daily_events.side_effect = fake_events

        result = asyncio.run(svc.run_full_pipeline(target_date=date(2026, 7, 7), dry_run=True))

    assert result["index_snapshot"]["value"] == 3300.0
    read_index.assert_called_once()
    read_zt.assert_called_once()
    read_quotes.assert_called_once()


def test_refresh_data_assets_cli_uses_snapshot_refresh():
    from backend import main as main_module

    args = SimpleNamespace(force=False)
    logger = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)

    with patch("backend.main.parse_watchlist", return_value=[{"code": "600000"}]), \
         patch("backend.main.refresh_market_snapshots", return_value={"status": "ok", "assets": {}}) as refresh:
        main_module._run_refresh_data_assets(args, logger)

    refresh.assert_called_once()
