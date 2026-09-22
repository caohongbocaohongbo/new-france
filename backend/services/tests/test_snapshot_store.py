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


def test_remote_stale_swr_and_backoff(monkeypatch):
    """真 SWR：过期后立即返回 stale（不等待上游），后台刷新；失败后退避窗口不再发请求。"""
    state = {"mode": "ok", "calls": 0}

    def fake_get(url, **kwargs):
        state["calls"] += 1
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
    policy = ss.RemotePolicy(ttl_seconds=0.0)  # 过期 → SWR 立即返回 stale，后台刷新
    stale = ss.fetch_remote_snapshot("k2", "http://x", policy)
    assert stale is not None and stale.stale and stale.refresh_error
    # SWR 不阻塞：即便上游本应耗时，此处调用已返回（后台线程在跑）
    time.sleep(1.0)  # 等后台刷新完成并进入退避
    calls_before = state["calls"]
    ss.fetch_remote_snapshot("k2", "http://x", policy)  # 退避窗口内不再发请求
    assert state["calls"] == calls_before


def test_swr_returns_stale_fast_while_upstream_slow(monkeypatch):
    """验收：上游休眠 400ms 时，陈旧缓存请求 <50ms 返回 stale=True；后台完成后读取新版本。"""
    state = {"sleep": 0.0, "calls": 0}

    def slow_get(url, **kwargs):
        state["calls"] += 1
        time.sleep(state["sleep"])
        return type("R", (), {
            "status_code": 200,
            "raise_for_status": lambda self=None: None,
            "json": lambda: {"version": state["calls"]},
            "content": b'{"version": 1}',
        })()

    monkeypatch.setattr(httpx, "get", slow_get)
    first = ss.fetch_remote_snapshot("k3", "http://x")
    assert first is not None and not first.stale
    state["sleep"] = 0.4
    t0 = time.perf_counter()
    stale = ss.fetch_remote_snapshot("k3", "http://x", ss.RemotePolicy(ttl_seconds=0.0))
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert stale is not None and stale.stale
    assert elapsed_ms < 50  # 不等待 400ms 上游
    time.sleep(0.6)  # 等后台完成
    fresh = ss.fetch_remote_snapshot("k3", "http://x", ss.RemotePolicy(ttl_seconds=300.0))
    assert fresh is not None and not fresh.stale  # 后台刷新完成，TTL 内命中新版本



def test_lru_multithread_and_oversized(tmp_path, monkeypatch):
    """P1-4：LRU 多线程读写 + 单条超上限跳过缓存。"""
    monkeypatch.setattr(ss, "_max_cache_bytes", lambda: 3000)
    paths = []
    for i in range(20):
        p = tmp_path / f"{i}.json"
        ss.atomic_write_json(p, {"i": i, "blob": "y" * 120})
        paths.append(p)
    errors = []

    def worker():
        try:
            for _ in range(30):
                for p in paths:
                    e = ss.read_snapshot_entry(p)
                    assert e is not None
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert ss.cache_stats()["bytes"] <= 3000
    # 超大单条：跳过缓存（字节上限真正生效）
    big = tmp_path / "big.json"
    big.write_bytes(b'{"blob": "' + b"z" * 10000 + b'"}')
    e = ss.read_snapshot_entry(big)
    assert e is not None
    assert ss.cache_stats()["bytes"] <= 3000


def test_invalid_remote_json_not_cached(monkeypatch):
    """P1 验收：非法远程 JSON 不得进入缓存。"""
    def bad_get(url, **kwargs):
        return type("R", (), {
            "status_code": 200,
            "raise_for_status": lambda self=None: None,
            "json": lambda: {"not": "dict"},
            "content": b"not json at all",
        })()

    monkeypatch.setattr(httpx, "get", bad_get)
    out = ss.fetch_remote_snapshot("badjson", "http://x")
    assert out is None
    assert ss.remote_cache_stats().get("badjson", {}).get("has_entry") is False


def test_singleflight_failure_consistent_stale(monkeypatch):
    """P0-2 验收：refresh 失败后所有调用者拿到一致 stale 状态。"""
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
    first = ss.fetch_remote_snapshot("k4", "http://x")
    assert first is not None and not first.stale
    state["mode"] = "fail"
    policy = ss.RemotePolicy(ttl_seconds=0.0)
    results = []
    barrier = threading.Barrier(10)
    threads = [threading.Thread(target=lambda: (barrier.wait(), results.append(ss.fetch_remote_snapshot("k4", "http://x", policy))))
               for _ in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    time.sleep(1.2)  # 等后台刷新失败完成
    assert all(r is not None and r.stale for r in results)  # 所有调用者一致 stale

