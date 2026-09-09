"""§2.4 read_snapshot_resilient 规格单测（离线：文件/网络全 mock，不联网）。"""
import json
from types import SimpleNamespace

import backend.plugins.common as common


def _setup_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(common, "DATA_BACKEND_DIR", tmp_path / "data_backend")
    monkeypatch.setattr(common, "REMOTE_CACHE_DIR", tmp_path / ".cache")
    (tmp_path / "data_backend").mkdir()


def test_step1_local_hit(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path)
    (tmp_path / "tech_indicators_latest.json").write_text(json.dumps({"status": "completed", "items": [1]}))
    out = common.read_snapshot_resilient("tech_indicators")
    assert out["_source"] == "local" and out["items"] == [1]


def test_step2_data_backend_hit(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path)
    (tmp_path / "data_backend" / "foo_latest.json").write_text(json.dumps({"status": "completed"}))
    out = common.read_snapshot_resilient("foo")
    assert out["_source"] == "local" and out["status"] == "completed"


def test_chip_intercept_no_network(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path)
    calls = []

    def fake_get(*a, **k):
        calls.append(1)
        raise AssertionError("chip 拦截下不应发网络")

    import httpx as _httpx
    monkeypatch.setattr(_httpx, "get", fake_get)
    out = common.read_snapshot_resilient("chip_scanner")
    assert out["status"] == "no_data" and out["_source"] == "local_only"
    assert out["reason"] == "chip_remote_fetch_disabled"
    assert not calls


def test_remote_success_writes_ttl_cache(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path)
    calls = {"n": 0}

    def fake_get(url, timeout=None):
        calls["n"] += 1
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {"status": "completed", "items": []})

    import httpx as _httpx
    monkeypatch.setattr(_httpx, "get", fake_get)
    out = common.read_snapshot_resilient("foo", ttl_seconds=60)
    assert out["_source"] == "snapshot" and out["status"] == "completed"
    # 第二次命中磁盘 TTL，不再发网络
    out2 = common.read_snapshot_resilient("foo", ttl_seconds=60)
    assert out2["_source"] == "snapshot" and calls["n"] == 1
    assert (tmp_path / ".cache" / "foo_remote.json").exists()


def test_ttl_zero_skips_cache(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path)
    calls = {"n": 0}

    def fake_get(url, timeout=None):
        calls["n"] += 1
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {"status": "completed"})

    import httpx as _httpx
    monkeypatch.setattr(_httpx, "get", fake_get)
    common.read_snapshot_resilient("foo", ttl_seconds=0)
    common.read_snapshot_resilient("foo", ttl_seconds=0)
    assert calls["n"] == 2
    assert not (tmp_path / ".cache" / "foo_remote.json").exists()


def test_remote_failure_shape(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path)

    def fake_get(url, timeout=None):
        raise ValueError("boom")

    import httpx as _httpx
    monkeypatch.setattr(_httpx, "get", fake_get)
    out = common.read_snapshot_resilient("foo")
    assert out["status"] == "no_data" and out["_source"] == "unavailable"
    assert out["reason"] == "remote_fetch_failed:err:ValueError"
    assert out["items"] == []


def test_never_raises_on_garbage(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path)
    (tmp_path / "foo_latest.json").write_text("{broken json")
    (tmp_path / "data_backend" / "foo_latest.json").write_text("[1,2]")

    import httpx as _httpx
    monkeypatch.setattr(_httpx, "get", lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: ["not", "dict"]))
    out = common.read_snapshot_resilient("foo")  # 不抛异常
    assert out["_source"] == "unavailable"
