from datetime import datetime, timedelta, timezone

import pandas as pd

from backend.services.data_backend import snapshots


BEIJING_TZ = timezone(timedelta(hours=8))


def test_refresh_market_snapshots_writes_local_and_report_snapshots(monkeypatch, tmp_path):
    monkeypatch.setattr(snapshots, "DATA_BACKEND_DIR", tmp_path / "data_backend")
    monkeypatch.setattr(snapshots, "REPORT_DATA_BACKEND_DIR", tmp_path / "reports" / "data_backend")
    monkeypatch.setattr(snapshots, "_now", lambda: datetime(2026, 7, 7, 14, 35, tzinfo=BEIJING_TZ))

    zt_pool = pd.DataFrame([{"代码": "600000", "名称": "浦发银行", "最新价": 10.0}])
    quotes = pd.DataFrame([{"代码": "600000", "名称": "浦发银行", "最新价": 10.1}])
    index_snapshot = {"code": "000001", "name": "上证指数", "value": 3300.0, "gain_pct": 0.5}

    result = snapshots.refresh_market_snapshots(
        watchlist_codes=["600000"],
        zt_fetcher=lambda: zt_pool,
        quotes_fetcher=lambda codes: quotes,
        index_fetcher=lambda: index_snapshot,
    )

    assert result["status"] == "ok"
    assert result["assets"]["zt_pool"]["record_count"] == 1
    assert (tmp_path / "data_backend" / "zt_pool.json").exists()
    assert (tmp_path / "reports" / "data_backend" / "zt_pool.json").exists()
    assert (tmp_path / "reports" / "data_backend" / "quotes.json").exists()
    assert (tmp_path / "reports" / "data_backend" / "index_snapshot.json").exists()

