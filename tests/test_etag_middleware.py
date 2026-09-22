"""ETag 契约测试（P1 迁移后）：目标快照接口早期 304；未迁移小接口不再有全局 ETag。"""
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import get_app
from backend.plugins.common import write_snapshot

PROJECT_DIR = Path(__file__).resolve().parent.parent
TIER_FILE = PROJECT_DIR / "reports" / "tier_flow_latest.json"


def test_unmigrated_small_endpoint_has_no_etag():
    """P1 §5.3：未迁移的小型动态接口暂不发 ETag，不保留昂贵的全局兜底。"""
    client = TestClient(get_app())
    r = client.get("/api/v1/l2/latest")
    assert r.status_code == 200
    assert r.headers.get("ETag") is None


def test_tier_flow_etag_304_when_unchanged():
    original = TIER_FILE.read_bytes() if TIER_FILE.exists() else None
    write_snapshot("tier_flow", {"status": "completed", "now": "2026-09-21T15:00:00+08:00",
                                 "states": {}, "items": [{"code": "600001", "name": "测试", "super_net": 1.0,
                                                          "big_net": 2.0, "smart_ratio": 3.0, "state": "吸筹"}]})
    try:
        client = TestClient(get_app())
        r1 = client.get("/api/v1/principal-capital/tier-flow/latest")
        assert r1.status_code == 200
        etag = r1.headers.get("ETag")
        assert etag and etag.startswith('W/"')
        r2 = client.get("/api/v1/principal-capital/tier-flow/latest", headers={"If-None-Match": etag})
        assert r2.status_code == 304
    finally:
        if original is None:
            TIER_FILE.unlink(missing_ok=True)
        else:
            TIER_FILE.write_bytes(original)


def test_tier_flow_etag_changes_when_snapshot_updates():
    original = TIER_FILE.read_bytes() if TIER_FILE.exists() else None
    try:
        write_snapshot("tier_flow", {"status": "completed", "now": "2026-09-21T15:00:00+08:00",
                                     "states": {}, "items": [{"code": "600001", "name": "测试", "super_net": 1.0,
                                                              "big_net": 2.0, "smart_ratio": 3.0, "state": "吸筹"}]})
        client = TestClient(get_app())
        etag1 = client.get("/api/v1/principal-capital/tier-flow/latest").headers.get("ETag")
        write_snapshot("tier_flow", {"status": "completed", "now": "2026-09-21T15:05:00+08:00",
                                     "states": {}, "items": [{"code": "600001", "name": "测试", "super_net": 9.0,
                                                              "big_net": 2.0, "smart_ratio": 3.0, "state": "吸筹"}]})
        etag2 = client.get("/api/v1/principal-capital/tier-flow/latest").headers.get("ETag")
        assert etag1 != etag2
    finally:
        if original is None:
            TIER_FILE.unlink(missing_ok=True)
        else:
            TIER_FILE.write_bytes(original)
