from datetime import datetime, timedelta, timezone

import pandas as pd

from backend.services.data_backend import snapshots


BEIJING_TZ = timezone(timedelta(hours=8))


def test_read_index_snapshot_uses_fresh_local_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(snapshots, "DATA_BACKEND_DIR", tmp_path)
    monkeypatch.setattr(snapshots, "REPORT_DATA_BACKEND_DIR", tmp_path / "reports")
    monkeypatch.setattr(snapshots, "_MEMORY_CACHE", {})
    monkeypatch.setattr(snapshots, "_now", lambda: datetime(2026, 7, 6, 10, 0, tzinfo=BEIJING_TZ))
    monkeypatch.setattr(
        snapshots,
        "_fetch_remote_snapshot_json",
        lambda asset: (_ for _ in ()).throw(AssertionError("不应读取远程快照")),
    )

    snapshots._write_local_snapshot(
        "index_snapshot",
        {
            "source": "eastmoney",
            "fetched_at": "2026-07-06T09:59:40+08:00",
            "records": {
                "code": "000001",
                "name": "上证指数",
                "value": 3300.12,
                "gain_pct": 0.88,
                "source": "东方财富",
                "fetched_at": "2026-07-06T09:59:40+08:00",
            },
        },
    )

    result, meta = snapshots.read_index_snapshot(fetcher=lambda: {"value": 1})

    assert result["value"] == 3300.12
    assert meta["status"] == "fresh"
    assert meta["source"] == "eastmoney"


def test_read_quotes_fetches_when_local_snapshot_is_expired(monkeypatch, tmp_path):
    monkeypatch.setattr(snapshots, "DATA_BACKEND_DIR", tmp_path)
    monkeypatch.setattr(snapshots, "REPORT_DATA_BACKEND_DIR", tmp_path / "reports")
    monkeypatch.setattr(snapshots, "_MEMORY_CACHE", {})
    monkeypatch.setattr(snapshots, "_now", lambda: datetime(2026, 7, 6, 10, 0, tzinfo=BEIJING_TZ))
    snapshots._write_local_snapshot(
        "quotes",
        {
            "source": "cache",
            "fetched_at": "2026-07-06T09:50:00+08:00",
            "records": [{"代码": "600000", "最新价": 10.0}],
        },
    )

    fetched = pd.DataFrame([{"代码": "600001", "名称": "浦发银行", "最新价": 12.3}])
    result, meta = snapshots.read_quotes(["600001"], fetcher=lambda codes: fetched)

    assert result["代码"].tolist() == ["600001"]
    assert meta["status"] == "fresh"
    assert meta["source"] == "live"


def test_read_zt_pool_falls_back_to_remote_snapshot_when_all_local_and_fetch_fail(monkeypatch, tmp_path):
    monkeypatch.setattr(snapshots, "DATA_BACKEND_DIR", tmp_path)
    monkeypatch.setattr(snapshots, "REPORT_DATA_BACKEND_DIR", tmp_path / "reports")
    monkeypatch.setattr(snapshots, "_MEMORY_CACHE", {})
    monkeypatch.setattr(snapshots, "_now", lambda: datetime(2026, 7, 6, 10, 0, tzinfo=BEIJING_TZ))
    monkeypatch.setattr(
        snapshots,
        "_fetch_remote_snapshot_json",
        lambda asset: {
            "source": "data-snapshots",
            "fetched_at": "2026-07-06T09:30:00+08:00",
            "records": [{"代码": "600000", "名称": "浦发银行", "最新价": 10.0}],
        },
    )

    result, meta = snapshots.read_zt_pool(fetcher=lambda: None)

    assert result["代码"].tolist() == ["600000"]
    assert meta["status"] == "degraded"
    assert meta["source"] == "data-snapshots"
    assert meta["degraded_from"] == "live"


def test_read_zt_pool_returns_unavailable_when_all_paths_are_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(snapshots, "DATA_BACKEND_DIR", tmp_path)
    monkeypatch.setattr(snapshots, "REPORT_DATA_BACKEND_DIR", tmp_path / "reports")
    monkeypatch.setattr(snapshots, "_MEMORY_CACHE", {})
    monkeypatch.setattr(snapshots, "_now", lambda: datetime(2026, 7, 6, 10, 0, tzinfo=BEIJING_TZ))
    monkeypatch.setattr(snapshots, "_fetch_remote_snapshot_json", lambda asset: None)

    result, meta = snapshots.read_zt_pool(fetcher=lambda: None)

    assert result is None
    assert meta["status"] == "unavailable"
    assert meta["source"] == "none"


def test_is_fresh_rejects_previous_day_snapshot_even_in_post_session(monkeypatch):
    monkeypatch.setattr(snapshots, "_now", lambda: datetime(2026, 7, 7, 15, 12, tzinfo=BEIJING_TZ))

    payload = {
        "fetched_at": "2026-07-06T17:43:40+08:00",
        "records": [{"代码": "000656"}],
    }

    assert snapshots._is_fresh("zt_pool", payload) is False


def test_is_fresh_accepts_same_day_snapshot_in_post_session(monkeypatch):
    monkeypatch.setattr(snapshots, "_now", lambda: datetime(2026, 7, 7, 15, 12, tzinfo=BEIJING_TZ))

    payload = {
        "fetched_at": "2026-07-07T14:59:40+08:00",
        "records": [{"代码": "000656"}],
    }

    assert snapshots._is_fresh("zt_pool", payload) is True


def test_is_fresh_rejects_same_day_snapshot_over_ttl_during_open_session(monkeypatch):
    monkeypatch.setattr(snapshots, "_now", lambda: datetime(2026, 7, 7, 10, 0, tzinfo=BEIJING_TZ))

    payload = {
        "fetched_at": "2026-07-07T09:50:00+08:00",
        "records": [{"代码": "000656"}],
    }

    assert snapshots._is_fresh("zt_pool", payload) is False
