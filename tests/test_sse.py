"""Phase 3 SSE 推送测试。"""
from backend.plugins import common
from backend.plugins.common import write_snapshot


def test_publish_snapshot_update_broadcasts(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr(common, "DATA_BACKEND_DIR", tmp_path / "reports" / "data_backend")
    q1 = common.subscribe_sse()
    q2 = common.subscribe_sse()
    write_snapshot("test_sse_plugin", {"status": "completed"})
    assert q1.get_nowait() == "test_sse_plugin"
    assert q2.get_nowait() == "test_sse_plugin"
    common.unsubscribe_sse(q1)
    common.unsubscribe_sse(q2)


def test_unsubscribe_removes_subscriber():
    q = common.subscribe_sse()
    common.unsubscribe_sse(q)
    assert q not in common._sse_subscribers
