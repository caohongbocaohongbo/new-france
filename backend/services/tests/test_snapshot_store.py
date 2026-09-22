"""P1 snapshot_store 单测：原子写 / LRU 字节缓存 / 早期 304 / 远程合并缓存。"""
import json
import threading
import time

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.services import snapshot_store as ss


@pytest.fixture(autouse=True)
def _clean():
    ss.reset_remote_cache()
    for key in list(ss._file_cache):
        ss.cache_invalidate(key)
    yield


def test_atomic_write_valid_and_cleanup(tmp_path):
    path = tmp_path / "x.json"
    meta = ss.atomic_write_json(path, {"status": "ok", "nan": float("nan"), "inf": float("inf")})
    assert meta.size > 0
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "ok" and data["nan"] is None and data["inf"] is None
    assert not list(tmp_path.glob("*.tmp"))  # 临时文件已清理


def test_atomic_write_replace_failure_keeps_old(monkeypatch, tmp_path):
    path = tmp_path / "x.json"
    ss.atomic_write_json(path, {"v": 1})
    real_replace = __import__("os").replace

    def failing_replace(src, dst):
        if "tmp" in str(src) and not hasattr(failing_replace, "_done"):
            failing_replace._done = True
            raise OSError("injected replace failure")
        return real_replace(src, dst)

    monkeypatch.setattr("backend.services.snapshot_store.os.replace", failing_replace)
    with pytest.raises(OSError):
        ss.atomic_write_json(path, {"v": 2})
    assert json.loads(path.read_text(encoding="utf-8"))["v"] == 1  # 旧正式文件仍可读


def test_concurrent_atomic_writes_same_file(tmp_path):
    path = tmp_path / "x.json"
    errors = []

    def worker(n):
        try:
            for _ in range(10):
                ss.atomic_write_json(path, {"writer": n, "payload": "x" * 100})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert json.loads(path.read_text(encoding="utf-8"))  # 正式文件永远完整 JSON


def test_cache_stat_hit_parse_once(tmp_path, monkeypatch):
    path = tmp_path / "x.json"
    ss.atomic_write_json(path, {"v": [1, 2, 3]})
    e1 = ss.read_snapshot_entry(path)
    stats1 = ss.cache_stats()
    e2 = ss.read_snapshot_entry(path)
    stats2 = ss.cache_stats()
    assert e1.etag == e2.etag
    assert e1.parsed() is e2.parsed()  # 内容不变只解析一次
    assert stats2["hits"] == stats1["hits"] + 1
    ss.atomic_write_json(path, {"v": [1, 2, 3, 4]})
    e3 = ss.read_snapshot_entry(path)
    assert e3.etag != e1.etag


def test_lru_byte_eviction(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "_max_cache_bytes", lambda: 300)
    paths = []
    for i in range(10):
        p = tmp_path / f"{i}.json"
        ss.atomic_write_json(p, {"i": i, "blob": "y" * 100})
        paths.append(p)
    stats = ss.cache_stats()
    assert stats["bytes"] <= 300
    assert stats["evictions"] > 0
    # 最老条目被逐出后仍可重新读取（miss 后回填）
    e = ss.read_snapshot_entry(paths[0])
    assert e is not None


def test_weak_etag_match():
    assert ss._etag_matches('W/"abc"', 'W/"abc"')
    assert ss._etag_matches('"abc"', 'W/"abc"')
    assert ss._etag_matches('W/"x", W/"abc"', 'W/"abc"')
    assert not ss._etag_matches(None, 'W/"abc"')


def test_early_304_via_testclient(tmp_path):
    path = tmp_path / "x.json"
    ss.atomic_write_json(path, {"status": "ok", "items": [1]})
    app = FastAPI()

    @app.get("/snap")
    def snap(request: Request):
        entry = ss.read_snapshot_entry(path)
        return ss.json_response_from_entry(request, entry)

    client = TestClient(app)
    r1 = client.get("/snap")
    assert r1.status_code == 200 and r1.headers["ETag"].startswith('W/"')
    etag = r1.headers["ETag"]
    r2 = client.get("/snap", headers={"If-None-Match": etag})
    assert r2.status_code == 304 and r2.headers["ETag"] == etag


def test_remote_singleflight_20_threads(monkeypatch):
    calls = {"n": 0}
    barrier = threading.Barrier(20)

    def fake_get(url, **kwargs):
        with calls and barrier:  # 防竞争写
            pass
        return type("R", (), {
            "status_code": 200,
            "raise_for_status": lambda self=None: None,
            "json": lambda: {"status": "completed"},
            "content": b'{"status": "completed"}',
        })()

    def fake_get_slow(url, **kwargs):
        calls["n"] += 1
        time.sleep(0.3)
        return type("R", (), {
            "status_code": 200,
            "raise_for_status": lambda self=None: None,
            "json": lambda: {"status": "completed"},
            "content": b'{"status": "completed"}',
        })()

    monkeypatch.setattr(httpx, "get", fake_get_slow)
    results = []
    threads = [threading.Thread(target=lambda: results.append(ss.fetch_remote_snapshot("k1", "http://x")))
               for _ in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert all(r is not None for r in results)
    assert calls["n"] == 1  # 同 key 并发只 1 次上游请求


def test_remote_stale_and_backoff(monkeypatch):
    state = {"mode": "ok"}

    def fake_get(url, **kwargs):
        if state["mode"] == "fail":
            raise ValueError("boom")
        return type("R", (), {
            "status_code": 200,
            "raise_for_status": lambda self=None: None,
            "json": lambda: {"status": "completed"},
            "content": b'{"status": "completed"}',
        })()

    monkeypatch.setattr(httpx, "get", fake_get)
    ok = ss.fetch_remote_snapshot("k2", "http://x")
    assert ok is not None and not ok.stale
    state["mode"] = "fail"
    policy = ss.RemotePolicy(ttl_seconds=0.0)  # 过期 → 触发刷新 → 失败 → stale
    stale = ss.fetch_remote_snapshot("k2", "http://x", policy)
    assert stale is not None and stale.stale and stale.refresh_error
    calls = {"n": 0}

    def counting_get(url, **kwargs):
        calls["n"] += 1
        raise ValueError("boom")

    monkeypatch.setattr(httpx, "get", counting_get)
    ss.fetch_remote_snapshot("k2", "http://x", policy)  # 退避窗口内不再请求
    assert calls["n"] == 0
