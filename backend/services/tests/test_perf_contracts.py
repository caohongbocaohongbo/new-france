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


def test_tier_flow_compact_default_contract(tier_client):
    r = tier_client.get("/api/v1/principal-capital/tier-flow/latest")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] and data["now"]
    assert data["returned"] <= 100 and data["limit"] == 100
    assert data["total"] > 0 and isinstance(data["states"], dict)
    for item in data["items"]:
        assert set(item) <= {"code", "name", "super_net", "big_net", "smart_ratio", "state"}
    # ETag 存在 + 304 早返回
    etag = r.headers.get("ETag")
    assert etag and etag.startswith('W/"')
    r2 = tier_client.get("/api/v1/principal-capital/tier-flow/latest", headers={"If-None-Match": etag})
    assert r2.status_code == 304
    # 分页
    r3 = tier_client.get("/api/v1/principal-capital/tier-flow/latest?offset=100&limit=50")
    assert r3.json()["offset"] == 100 and r3.json()["returned"] <= 50


def test_tier_flow_limits_and_full(tier_client):
    assert tier_client.get("/api/v1/principal-capital/tier-flow/latest?limit=201").status_code == 422
    assert tier_client.get("/api/v1/principal-capital/tier-flow/latest?view=bogus").status_code == 422
    assert tier_client.get("/api/v1/principal-capital/tier-flow/latest?fields=evil_field").status_code == 422
    r = tier_client.get("/api/v1/principal-capital/tier-flow/latest?view=full")
    assert r.status_code == 200 and len(r.json()["items"]) > 100  # full 保留全量


from backend.plugins.overnight_arbitrage.router import router as oa_router  # noqa: E402


@pytest.fixture(scope="module")
def oa_client():
    app = FastAPI()
    app.include_router(oa_router, prefix="/api/v1/overnight-arbitrage")
    return TestClient(app)


def test_oa_latest_compact_contract(oa_client):
    r = oa_client.get("/api/v1/overnight-arbitrage/latest")
    assert r.status_code == 200
    data = r.json()
    assert "status" in data and "results" in data
    assert "data_quality" not in data  # compact 默认剔除大字段
    for it in data["results"]:
        for field in ("action", "decision_score", "current_price", "reasons", "risks"):
            assert field in it
    r2 = oa_client.get("/api/v1/overnight-arbitrage/latest", headers={"If-None-Match": r.headers["ETag"]})
    assert r2.status_code == 304


def test_oa_history_pagination_and_detail(oa_client):
    r = oa_client.get("/api/v1/overnight-arbitrage/history")
    assert r.status_code == 200
    data = r.json()
    assert data["returned"] <= 100 and data["limit"] == 100
    for rec in data["records"]:
        assert "recommendations" not in rec  # 默认不携带嵌套长明细
        assert "price_pushes" not in rec and "pe_values" not in rec  # 数值序列走单股明细
    assert oa_client.get("/api/v1/overnight-arbitrage/history?limit=201").status_code == 422
    # 单股明细完整返回
    if data["records"]:
        code = data["records"][0]["code"]
        d = oa_client.get(f"/api/v1/overnight-arbitrage/history?code={code}").json()
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


def test_sqlite_concurrent_readers_writers():
    from sqlalchemy import text

    from backend.db.database import engine, init_db

    init_db()
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS perf_stress"))
        conn.execute(text("CREATE TABLE perf_stress (id INTEGER PRIMARY KEY, v TEXT)"))
    errors = []

    def reader():
        try:
            for _ in range(50):
                with engine.connect() as conn:
                    conn.execute(text("SELECT COUNT(*) FROM perf_stress")).scalar()
        except Exception as exc:  # noqa: BLE001
            errors.append(("reader", exc))

    def writer(wid):
        try:
            for i in range(100):
                with engine.begin() as conn:
                    conn.execute(text("INSERT INTO perf_stress (v) VALUES (:v)"), {"v": f"w{wid}-{i}"})
        except Exception as exc:  # noqa: BLE001
            errors.append(("writer", exc))

    threads = [threading.Thread(target=reader) for _ in range(20)] + [threading.Thread(target=writer, args=(w,)) for w in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors  # 无未处理的 database is locked
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS perf_stress"))
