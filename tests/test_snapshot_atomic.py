"""快照原子写测试（Phase 1）。"""
from backend.plugins.common import read_snapshot, write_snapshot


def test_write_snapshot_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.plugins.common.REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr("backend.plugins.common.DATA_BACKEND_DIR", tmp_path / "reports" / "data_backend")
    payload = {"status": "completed", "now": "2026-08-18T10:00:00+08:00", "items": [{"a": 1}]}
    write_snapshot("test_plugin", payload)
    assert read_snapshot("test_plugin") == payload
    # 无 .tmp 残留（原子 rename）
    assert list(tmp_path.rglob("*.tmp")) == []


def test_write_snapshot_atomic_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.plugins.common.REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr("backend.plugins.common.DATA_BACKEND_DIR", tmp_path / "reports" / "data_backend")
    write_snapshot("test_plugin", {"status": "v1"})
    write_snapshot("test_plugin", {"status": "v2"})
    assert read_snapshot("test_plugin") == {"status": "v2"}
    assert list(tmp_path.rglob("*.tmp")) == []
