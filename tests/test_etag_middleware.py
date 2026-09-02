"""Phase 2 ETag 中间件测试。"""
from fastapi.testclient import TestClient

from backend.main import get_app
from backend.plugins.common import write_snapshot


def test_etag_returns_304_when_unchanged():
    write_snapshot("l2_research", {"status": "completed", "now": "2026-08-18T10:00:00+08:00", "milestone": "M1", "sources": []})
    client = TestClient(get_app())
    r1 = client.get("/api/v1/l2/latest")
    assert r1.status_code == 200
    etag = r1.headers.get("ETag")
    assert etag
    r2 = client.get("/api/v1/l2/latest", headers={"If-None-Match": etag})
    assert r2.status_code == 304


def test_etag_changes_when_snapshot_updates():
    write_snapshot("l2_research", {"status": "completed", "now": "2026-08-18T10:00:00+08:00", "milestone": "v1"})
    client = TestClient(get_app())
    r1 = client.get("/api/v1/l2/latest")
    etag1 = r1.headers.get("ETag")
    write_snapshot("l2_research", {"status": "completed", "now": "2026-08-18T10:05:00+08:00", "milestone": "v2"})
    r2 = client.get("/api/v1/l2/latest")
    etag2 = r2.headers.get("ETag")
    assert etag1 != etag2
