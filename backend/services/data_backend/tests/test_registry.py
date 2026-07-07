import json
from pathlib import Path

from backend.services.data_backend import registry
from backend.services.data_backend import snapshots


def test_get_all_assets_returns_complete_fields_for_available_and_missing_sources(monkeypatch, tmp_path):
    health_path = tmp_path / "source_health.json"
    health_path.write_text(
        json.dumps(
            {
                "sources": {
                    "national_team": {
                        "source_status": {
                            "source": "eastmoney",
                            "fetched_at": "2026-07-06T15:09:00+08:00",
                            "errors": [],
                        },
                        "data": {"record_count": 18},
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cache_path = tmp_path / "principal_capital_cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "fetched_at": "2026-07-06T15:08:30+08:00",
                "records": [{"code": "600000"}, {"code": "600001"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    principal_health_path = tmp_path / "principal_capital_source_health.json"
    principal_health_path.write_text(
        json.dumps(
            {
                "eastmoney": {"failure_streak": 0, "blocked_until": None},
                "updated_at": "2026-07-06T15:08:31+08:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(registry, "OPTIONAL_SOURCE_HEALTH_FILE", health_path)
    monkeypatch.setattr(registry, "PRINCIPAL_CAPITAL_CACHE_FILE", cache_path)
    monkeypatch.setattr(registry, "PRINCIPAL_CAPITAL_HEALTH_FILE", principal_health_path)
    monkeypatch.setattr(registry, "_read_latest_screening_payload", lambda: {})
    monkeypatch.setattr(registry, "_now", lambda: registry._parse_dt("2026-07-06T15:10:00+08:00"))
    # 隔离 data_backend 快照目录, 避免读到真实快照文件; zt_pool 无本地快照即 unavailable
    empty_dir = tmp_path / "data_backend"
    empty_dir.mkdir()
    monkeypatch.setattr(snapshots, "DATA_BACKEND_DIR", empty_dir)
    monkeypatch.setattr(snapshots, "REPORT_DATA_BACKEND_DIR", empty_dir)
    monkeypatch.setattr(snapshots, "_MEMORY_CACHE", {})

    assets = registry.get_all_assets()
    asset_map = {item["asset"]: item for item in assets}

    expected_assets = {
        "zt_pool",
        "quotes",
        "index_snapshot",
        "principal_capital",
        "historical_kline",
        "national_team",
    }
    assert set(asset_map) == expected_assets

    for item in assets:
        assert set(item) == {
            "asset",
            "source",
            "fetched_at",
            "age_seconds",
            "record_count",
            "status",
            "trading_session",
            "degraded_from",
            "error",
        }

    assert asset_map["principal_capital"]["source"] == "eastmoney"
    assert asset_map["principal_capital"]["record_count"] == 2
    assert asset_map["principal_capital"]["status"] == "fresh"
    assert asset_map["national_team"]["source"] == "eastmoney"
    assert asset_map["national_team"]["record_count"] == 18
    assert asset_map["zt_pool"]["status"] == "unavailable"

