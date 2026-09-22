"""P2 契约测试：tier-flow / oa latest / oa history compact 契约 + P3 SQLite 稳定性。"""
import json
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.plugins.principal_capital.router import router as tier_router


@pytest.fixture(scope="module")
def tier_client():
    app = FastAPI()
    app.include_router(tier_router, prefix="/api/v1/principal-capital")
    return TestClient(app)


def test_tier_flow_default_full_and_explicit_compact(tier_client):
    # P1-1 兼容：默认 full（旧调用方结构不变）
    full = tier_client.get("/api/v1/principal-capital/tier-flow/latest")
    assert full.status_code == 200
    full_data = full.json()
    assert "items" in full_data and len(full_data["items"]) > 100  # full 保留全量
    # 显式 compact：字段契约 + 分页 + 304
    r = tier_client.get("/api/v1/principal-capital/tier-flow/latest?view=compact")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] and data["now"]
    assert data["returned"] <= 100 and data["limit"] == 100
    assert data["total"] > 0 and isinstance(data["states"], dict)
    for item in data["items"]:
        assert set(item) <= {"code", "name", "super_net", "big_net", "smart_ratio", "state"}
    etag = r.headers.get("ETag")
    assert etag and etag.startswith('W/"')
    r2 = tier_client.get("/api/v1/principal-capital/tier-flow/latest?view=compact", headers={"If-None-Match": etag})
    assert r2.status_code == 304
    r3 = tier_client.get("/api/v1/principal-capital/tier-flow/latest?view=compact&offset=100&limit=50")
    assert r3.json()["offset"] == 100 and r3.json()["returned"] <= 50


def test_tier_flow_limits(tier_client):
    assert tier_client.get("/api/v1/principal-capital/tier-flow/latest?limit=201").status_code == 422
    assert tier_client.get("/api/v1/principal-capital/tier-flow/latest?view=bogus").status_code == 422
    assert tier_client.get("/api/v1/principal-capital/tier-flow/latest?fields=evil_field").status_code == 422


from backend.plugins.overnight_arbitrage.router import router as oa_router  # noqa: E402


@pytest.fixture(scope="module")
def oa_client():
    app = FastAPI()
    app.include_router(oa_router, prefix="/api/v1/overnight-arbitrage")
    return TestClient(app)


def test_oa_latest_default_full_explicit_compact(oa_client):
    # P1-1 兼容：默认 full
    full = oa_client.get("/api/v1/overnight-arbitrage/latest")
    assert full.status_code == 200 and "results" in full.json()
    # 显式 compact
    r = oa_client.get("/api/v1/overnight-arbitrage/latest?view=compact")
    assert r.status_code == 200
    data = r.json()
    assert "status" in data and "results" in data
    assert "data_quality" not in data  # compact 剔除大字段
    for it in data["results"]:
        for field in ("action", "decision_score", "current_price", "reasons", "risks"):
            assert field in it
    r2 = oa_client.get("/api/v1/overnight-arbitrage/latest?view=compact",
                       headers={"If-None-Match": r.headers["ETag"]})
    assert r2.status_code == 304


def test_oa_history_default_full_explicit_compact(oa_client):
    # 迁移期默认 full：保留嵌套明细
    full = oa_client.get("/api/v1/overnight-arbitrage/history?limit=5")
    assert full.status_code == 200
    full_records = full.json()["records"]
    assert full_records and "recommendations" in full_records[0]
    # 显式 compact：无嵌套明细/数值序列
    r = oa_client.get("/api/v1/overnight-arbitrage/history?view=compact&limit=100")
    data = r.json()
    assert data["returned"] <= 100 and data["limit"] == 100
    for rec in data["records"]:
        assert "recommendations" not in rec
        assert "price_pushes" not in rec and "pe_values" not in rec
    assert oa_client.get("/api/v1/overnight-arbitrage/history?limit=201").status_code == 422
    # 单股明细完整返回（与列表同一 full 数据源）
    code = data["records"][0]["code"]
    d = oa_client.get(f"/api/v1/overnight-arbitrage/history?view=compact&code={code}").json()
    assert d["total"] >= 1 and all(x["code"] == code for x in d["records"])
    detail = oa_client.get(f"/api/v1/overnight-arbitrage/{code}/history").json()
    assert detail["status"] == "ok" and "recommendations" in detail["record"]


def test_screening_latest_etag():
    from backend.api.router_screening import router as screening_router

    app = FastAPI()
    app.include_router(screening_router, prefix="/api/v1/screening")
    client = TestClient(app)
    r = client.get("/api/v1/screening/latest")
    assert r.status_code == 200 and r.headers.get("ETag")
    r2 = client.get("/api/v1/screening/latest", headers={"If-None-Match": r.headers["ETag"]})
    assert r2.status_code == 304


def test_screening_summary_view_contract():
    from backend.api.router_screening import router as screening_router

    app = FastAPI()
    app.include_router(screening_router, prefix="/api/v1/screening")
    client = TestClient(app)
    full = client.get("/api/v1/screening/latest").json()
    summary = client.get("/api/v1/screening/latest?view=summary").json()
    if "report_md" in full:
        assert "report_md" not in summary and "report_html" not in summary
    assert client.get("/api/v1/screening/latest?view=bogus").status_code == 422
    # 契约保留：audit/price_history/factors 不得被 summary 误删
    items = summary.get("results") or []
    if items:
        assert "factors" in items[0] and "audit" in items[0] and "price_history" in items[0]


# ==== P3 SQLite 稳定性 ====

def test_sqlite_wal_and_pragmas():
    from sqlalchemy import text

    from backend.db.database import _journal_mode, engine, init_db

    init_db()
    with engine.connect() as conn:
        assert _journal_mode(conn) == "wal"
        assert int(conn.execute(text("PRAGMA busy_timeout")).scalar()) == 5000
        assert int(conn.execute(text("PRAGMA synchronous")).scalar()) == 1


def test_sqlite_concurrent_readers_writers(tmp_path):
    """P3 验收：独立临时数据库 + 独立 engine（不触碰真实 data/new_france.db）。"""
    from sqlalchemy import create_engine, text

    from backend.db.database import apply_sqlite_pragmas

    tmp_engine = create_engine(
        f"sqlite:///{tmp_path / 'stress.db'}",
        echo=False,
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    apply_sqlite_pragmas(tmp_engine)
    with tmp_engine.begin() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("CREATE TABLE perf_stress (id INTEGER PRIMARY KEY, v TEXT)"))
    errors = []

    def reader():
        try:
            for _ in range(50):
                with tmp_engine.connect() as conn:
                    conn.execute(text("SELECT COUNT(*) FROM perf_stress")).scalar()
        except Exception as exc:  # noqa: BLE001
            errors.append(("reader", exc))

    def writer(wid):
        try:
            for i in range(100):
                with tmp_engine.begin() as conn:
                    conn.execute(text("INSERT INTO perf_stress (v) VALUES (:v)"), {"v": f"w{wid}-{i}"})
        except Exception as exc:  # noqa: BLE001
            errors.append(("writer", exc))

    threads = [threading.Thread(target=reader) for _ in range(20)] + [threading.Thread(target=writer, args=(w,)) for w in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors  # 无未处理的 database is locked
    tmp_engine.dispose()


# ==== 核验新增回归（2026-09-22） ====

def test_screening_compact_size_budget():
    """screening view=compact：列表字段响应 ≤100KB（合成 100 只重字段样本）。"""
    import json as _json

    from backend.api.router_screening import REPORTS_DIR, router as screening_router

    app = FastAPI()
    app.include_router(screening_router, prefix="/api/v1/screening")
    client = TestClient(app)
    cache_file = REPORTS_DIR / "latest.json"
    original = cache_file.read_bytes() if cache_file.exists() else None
    heavy_item = {
        "rank": 1, "code": "600001", "name": "测试", "adjusted_score": 90, "drop_pct": 5,
        "recommendation": "BUY", "zt_date": "2026-09-21",
        "factors": {"pullback": {"passed": True, "name": "回撤", "detail": "ok"}},
        "audit": {"downgraded": False, "validations": [{"status": "pass"}]},
        "price_history": [{"date": "2026-09-21", "close": 10.0}] * 40,
        "evidence": {"x": "y" * 2000},
    }
    payload = {
        "status": "completed", "date": "2026-09-21", "index_gain": 1.0,
        "strong_buy": 1, "buy": 99, "watch": 0, "total_scored": 100, "errors": [],
        "report_md": "x" * 20000, "report_html": "y" * 20000,
        "optional_sources": {"a": "b" * 10000},
        "results": [{**heavy_item, "code": f"600{i:03d}"} for i in range(100)],
    }
    try:
        cache_file.write_text(_json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        from backend.services import snapshot_store as ss

        ss.cache_invalidate(cache_file)
        r = client.get("/api/v1/screening/latest?view=compact")
        assert r.status_code == 200
        assert len(r.content) <= 100_000  # 体积门槛
        data = r.json()
        item = data["results"][0]
        assert "price_history" not in item and "evidence" not in item
        assert "factors" in item and "audit" in item  # 列表渲染契约保留
    finally:
        if original is None:
            cache_file.unlink(missing_ok=True)
        else:
            cache_file.write_bytes(original)


def test_principal_capital_and_task_history_no_sync_requests(monkeypatch):
    """P0-3：principal-capital / task-history 远程回退不得再直接同步 requests.get。"""
    import requests as _requests

    calls = []

    def _boom(*a, **k):
        calls.append(a)
        raise AssertionError("检测到同步 requests.get")

    monkeypatch.setattr(_requests, "get", _boom)
    from backend.plugins.principal_capital.service import (
        read_history_resilient, read_report_resilient, read_source_health_resilient,
    )
    from backend.services.task_history import read_task_history_resilient

    read_report_resilient()
    read_history_resilient()
    read_source_health_resilient()
    read_task_history_resilient()
    assert not calls


def test_oa_remote_list_and_detail_consistency(monkeypatch, tmp_path):
    """P1-2：OA 列表与单股详情使用同一远程 entry（Render 本地为空时一致）。"""
    from backend.plugins.overnight_arbitrage import router as oa_mod
    from backend.services.snapshot_store import SnapshotEntry, weak_etag

    monkeypatch.setattr(oa_mod, "HISTORY_FILE", tmp_path / "hist.json")
    monkeypatch.setattr(oa_mod, "HISTORY_COMPACT_FILE", tmp_path / "hist_compact.json")
    remote_payload = {"status": "completed", "total_stocks": 2,
                      "records": [
                          {"code": "600001", "name": "A", "recommendation_count": 2,
                           "last_recommended_at": "2026-09-20 14:43:00",
                           "recommendations": [{"action": "BUY"}]},
                          {"code": "600002", "name": "B", "recommendation_count": 1,
                           "last_recommended_at": "2026-09-19 14:43:00",
                           "recommendations": [{"action": "WATCH"}]},
                      ]}
    data = json.dumps(remote_payload, ensure_ascii=False).encode("utf-8")
    entry = SnapshotEntry(path=None, payload_bytes=data, etag=weak_etag(data),
                          stat=(0, len(data), 0), loaded_at=0, source="remote")
    from backend.services import snapshot_store as ss

    monkeypatch.setattr(ss, "fetch_remote_snapshot", lambda key, url, policy=None: entry)
    app = FastAPI()
    app.include_router(oa_mod.router, prefix="/api/v1/overnight-arbitrage")
    client = TestClient(app)
    lst = client.get("/api/v1/overnight-arbitrage/history?view=full&limit=10").json()
    detail = client.get("/api/v1/overnight-arbitrage/600001/history").json()
    assert {r["code"] for r in lst["records"]} == {"600001", "600002"}
    assert detail["status"] == "ok" and detail["record"]["recommendations"][0]["action"] == "BUY"

