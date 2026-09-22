"""P1 统一快照存储层：原子写 + LRU 字节缓存 + 早期 304 + 远程合并缓存。

设计约束（性能优化施工方案 §3.1/§3.3）：
- 序列化内部完整实现 NaN/Infinity 清理，不引用外部 _json_safe；
- 临时文件名含 PID+UUID；flush+fsync 后 os.replace，目录 fsync best-effort；
- LRU 按总字节上限（默认 32MB）逐出；未变更的无过滤响应直接返回预序列化 bytes；
- 远程快照：正缓存 TTL / 负退避+抖动 / 条件 GET / 同 key single-flight / stale-while-revalidate。
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
BEIJING_TZ = timezone(timedelta(hours=8))


def _now_iso() -> str:
    return datetime.now(BEIJING_TZ).isoformat()


# ==== JSON 安全序列化（完整实现，勿引外部 _json_safe） ====

def json_safe_value(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: json_safe_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe_value(v) for v in value]
    return value


def dumps_bytes(payload, indent: int = 2) -> bytes:
    return json.dumps(json_safe_value(payload), ensure_ascii=False, indent=indent, default=str).encode("utf-8")


def weak_etag(data: bytes) -> str:
    """弱 ETag：内容 SHA-256 前 16 位。"""
    return 'W/"' + hashlib.sha256(data).hexdigest()[:16] + '"'


def etag_for_params(entry_etag: str, canonical_params: str) -> str:
    """动态过滤接口的 ETag = 底层快照版本 + 规范化查询参数（不对响应体重复哈希）。"""
    return weak_etag((str(entry_etag) + "|" + str(canonical_params)).encode("utf-8"))


# ==== 原子写 ====

@dataclass
class SnapshotMeta:
    path: Path
    size: int
    mtime_ns: int
    inode: int
    etag: str
    written_at: str


def _stat_signature(path: Path) -> Optional[tuple]:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, getattr(st, "st_ino", 0))


def atomic_write_json(path: Path, payload: dict, indent: int = 2) -> SnapshotMeta:
    """原子写：PID+UUID 临时文件 → flush+fsync → os.replace → 目录 fsync best-effort。

    并发写同一文件时临时名不冲突；正式文件永远是完整 JSON。写入后主动刷新缓存。
    """
    data = dumps_bytes(payload, indent)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    try:  # 目录 fsync best-effort
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass
    sig = _stat_signature(path) or (0, len(data), 0)
    meta = SnapshotMeta(path=path, size=len(data), mtime_ns=sig[0], inode=sig[2],
                        etag=weak_etag(data), written_at=_now_iso())
    _file_cache_put(str(path), SnapshotEntry(path=path, payload_bytes=data, etag=meta.etag,
                                              stat=sig, loaded_at=time.time(), source="local"))
    return meta


# ==== LRU 字节缓存 ====

@dataclass
class SnapshotEntry:
    path: Optional[Path]
    payload_bytes: bytes
    etag: str
    stat: tuple
    loaded_at: float
    source: str = "local"  # local | remote
    stale: bool = False
    upstream_etag: Optional[str] = None
    upstream_last_modified: Optional[str] = None
    fetched_at: Optional[str] = None
    refresh_error: Optional[str] = None
    _parsed: Optional[dict] = field(default=None, repr=False)

    def parsed(self) -> dict:
        """惰性解析（只读契约：调用方不得原地修改）。"""
        if self._parsed is None:
            self._parsed = json.loads(self.payload_bytes.decode("utf-8"))
        return self._parsed


def _max_cache_bytes() -> int:
    try:
        return int(os.environ.get("SNAPSHOT_CACHE_MAX_BYTES", 32 * 1024 * 1024))
    except ValueError:
        return 32 * 1024 * 1024


_file_cache: OrderedDict = OrderedDict()
_file_cache_bytes = 0
_cache_stats = {"hits": 0, "misses": 0, "evictions": 0, "refreshes": 0}


def _file_cache_put(key: str, entry: SnapshotEntry) -> None:
    global _file_cache_bytes
    if key in _file_cache:
        _file_cache_bytes -= len(_file_cache.pop(key).payload_bytes)
    _file_cache[key] = entry
    _file_cache.move_to_end(key)
    _file_cache_bytes += len(entry.payload_bytes)
    limit = _max_cache_bytes()
    while _file_cache_bytes > limit and len(_file_cache) > 1:
        _old_key, old_entry = _file_cache.popitem(last=False)
        _file_cache_bytes -= len(old_entry.payload_bytes)
        _cache_stats["evictions"] += 1


def cache_invalidate(path) -> None:
    global _file_cache_bytes
    key = str(path)
    entry = _file_cache.pop(key, None)
    if entry is not None:
        _file_cache_bytes -= len(entry.payload_bytes)


def cache_stats() -> dict:
    return {"hits": _cache_stats["hits"], "misses": _cache_stats["misses"],
            "evictions": _cache_stats["evictions"], "refreshes": _cache_stats["refreshes"],
            "entries": len(_file_cache), "bytes": _file_cache_bytes,
            "max_bytes": _max_cache_bytes()}


def read_snapshot_entry(path: Path, source: str = "local") -> Optional[SnapshotEntry]:
    """读快照 entry：stat 签名命中直接返回缓存（内容不变时只解析一次）；否则重读并刷新。"""
    path = Path(path)
    if not path.exists():
        return None
    sig = _stat_signature(path)
    if sig is None:
        return None
    key = str(path)
    cached = _file_cache.get(key)
    if cached is not None and cached.stat == sig:
        _cache_stats["hits"] += 1
        _file_cache.move_to_end(key)
        return cached
    _cache_stats["misses"] += 1
    try:
        data = path.read_bytes()
    except OSError as exc:  # noqa: BLE001
        logger.warning("快照读取失败(%s): %s", path, exc)
        return None
    entry = SnapshotEntry(path=path, payload_bytes=data, etag=weak_etag(data),
                          stat=sig, loaded_at=time.time(), source=source)
    _cache_stats["refreshes"] += 1 if cached is not None else 0
    _file_cache_put(key, entry)
    return entry


# ==== 早期 304 响应 ====

def _etag_matches(header_value: Optional[str], etag: str) -> bool:
    if not header_value:
        return False
    for item in str(header_value).split(","):
        candidate = item.strip()
        if candidate == etag or candidate.lstrip("W/").strip('"') == etag.lstrip("W/").strip('"'):
            return True
    return False


def _base_response_headers(etag: str, cache_control: str, vary=None, extra_headers: dict = None) -> dict:
    headers = {"ETag": etag, "Cache-Control": cache_control}
    if vary:
        headers["Vary"] = vary
    if extra_headers:
        headers.update(extra_headers)
    return headers


def json_response_from_entry(request, entry: SnapshotEntry, cache_control: str = "no-cache",
                             vary=None, extra_headers: dict = None):
    """无过滤接口：304 在 JSON 解析/序列化前返回；未命中直接回预序列化 bytes。"""
    from fastapi import Response

    headers = _base_response_headers(entry.etag, cache_control, vary, extra_headers)
    if _etag_matches(request.headers.get("if-none-match"), entry.etag):
        return Response(status_code=304, headers=headers)
    return Response(content=entry.payload_bytes, media_type="application/json", headers=headers)


def json_response_from_bytes(request, data: bytes, etag: str, cache_control: str = "no-cache",
                             vary=None, extra_headers: dict = None):
    """过滤/动态接口：调用方已序列化 bytes 且已生成版本化 ETag；304 不重算。"""
    from fastapi import Response

    headers = _base_response_headers(etag, cache_control, vary, extra_headers)
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return Response(content=data, media_type="application/json", headers=headers)


# ==== 远程快照合并缓存（single-flight / 退避 / 条件 GET / stale-while-revalidate） ====

@dataclass
class RemotePolicy:
    ttl_seconds: float = 300.0
    connect_timeout: float = 1.5
    total_timeout: float = 3.0
    backoff_seconds: tuple = (3, 10, 30, 60)
    jitter: float = 0.2
    stale_ok: bool = True


def remote_policy_for(key: str) -> RemotePolicy:
    """按快照类别选择 TTL：history 600s / source health 60s / 其余 300s。"""
    if "history" in str(key):
        return RemotePolicy(ttl_seconds=600.0)
    if "health" in str(key) or "source_health" in str(key):
        return RemotePolicy(ttl_seconds=60.0)
    return RemotePolicy()


_remote_state: dict = {}
_remote_guard = threading.Lock()


def _mark_stale(entry: SnapshotEntry, refresh_error: str) -> SnapshotEntry:
    """返回 stale 标注副本（不原地修改共享缓存对象）。"""
    return SnapshotEntry(path=entry.path, payload_bytes=entry.payload_bytes, etag=entry.etag,
                         stat=entry.stat, loaded_at=entry.loaded_at, source=entry.source,
                         stale=True, upstream_etag=entry.upstream_etag,
                         upstream_last_modified=entry.upstream_last_modified,
                         fetched_at=entry.fetched_at, refresh_error=refresh_error,
                         _parsed=entry._parsed)


def fetch_remote_snapshot(key: str, url: str, policy: RemotePolicy = None) -> Optional[SnapshotEntry]:
    """同 key single-flight 远程快照：正缓存 TTL、负退避+抖动、条件 GET、stale-while-revalidate。

    返回 SnapshotEntry 或 None；上游失败但存在可用陈旧数据时返回 stale 副本（refresh_error 标注）。
    """
    policy = policy or remote_policy_for(key)
    now = time.time()
    with _remote_guard:
        state = _remote_state.setdefault(key, {
            "entry": None, "fetched_at": 0.0, "fail_count": 0,
            "next_retry_at": 0.0, "inflight": False, "event": threading.Event(),
        })
        entry = state["entry"]
        # 正缓存命中（TTL 内）
        if entry is not None and now - state["fetched_at"] < policy.ttl_seconds:
            return entry
        # 负退避窗口：不发起请求
        if now < state["next_retry_at"]:
            if entry is not None and policy.stale_ok:
                return _mark_stale(entry, f"backoff_until_{int(state['next_retry_at'] - now)}s")
            return None
        # single-flight：已有在途请求 → 等待其完成并复用结果
        if state["inflight"]:
            event = state["event"]
            waiting = True
        else:
            state["inflight"] = True
            state["event"].clear()
            waiting = False
    if waiting:
        event.wait(timeout=policy.total_timeout + 2.0)
        with _remote_guard:
            waited_entry = state["entry"]
        if waited_entry is not None:
            return waited_entry
        return None
    try:
        headers = {}
        if entry is not None and entry.upstream_etag:
            headers["If-None-Match"] = entry.upstream_etag
        if entry is not None and entry.upstream_last_modified:
            headers["If-Modified-Since"] = entry.upstream_last_modified
        import httpx
        resp = httpx.get(url, headers=headers or None,
                         timeout=httpx.Timeout(policy.total_timeout, connect=policy.connect_timeout))
        if resp.status_code == 304 and entry is not None:
            # 条件 GET 命中：延长本地 TTL，不重置内容
            state["fetched_at"] = now
            state["fail_count"] = 0
            state["next_retry_at"] = 0.0
            return entry
        resp.raise_for_status()
        data = getattr(resp, "content", None)
        if data is None:  # 兼容返回 json 对象的测试替身
            data = json.dumps(resp.json()).encode("utf-8")
        if not data:
            raise ValueError("远端响应为空")
        resp_headers = getattr(resp, "headers", None) or {}
        get_h = getattr(resp_headers, "get", None)
        new_entry = SnapshotEntry(path=None, payload_bytes=data, etag=weak_etag(data),
                                  stat=(0, len(data), 0), loaded_at=now, source="remote",
                                  upstream_etag=get_h("etag") if get_h else None,
                                  upstream_last_modified=get_h("last-modified") if get_h else None,
                                  fetched_at=_now_iso())
        state["entry"] = new_entry
        state["fetched_at"] = now
        state["fail_count"] = 0
        state["next_retry_at"] = 0.0
        return new_entry
    except Exception as exc:  # noqa: BLE001
        state["fail_count"] += 1
        idx = min(state["fail_count"] - 1, len(policy.backoff_seconds) - 1)
        base = float(policy.backoff_seconds[idx])
        state["next_retry_at"] = now + base + random.uniform(0, base * policy.jitter)
        code = ""
        resp_obj = getattr(exc, "response", None)
        if resp_obj is not None and getattr(resp_obj, "status_code", None):
            code = str(resp_obj.status_code)
        state["last_error"] = f"{type(exc).__name__}:{code}"
        logger.info("远程快照拉取失败(%s): %s %s", key, type(exc).__name__, code)
        if entry is not None and policy.stale_ok:
            return _mark_stale(entry, f"{type(exc).__name__}:{code}")
        return None
    finally:
        state["inflight"] = False
        state["event"].set()  # 唤醒 single-flight 等待者（成功或失败均需唤醒）


def remote_last_error(key: str) -> str:
    """最近一次上游失败类型（供调用方组装 reason，保持 §2.4 响应形状）。"""
    with _remote_guard:
        state = _remote_state.get(key) or {}
        return state.get("last_error") or "upstream_unavailable"


def reset_remote_cache() -> None:
    """测试用：清空远程合并缓存状态。"""
    with _remote_guard:
        _remote_state.clear()


def remote_cache_stats() -> dict:
    with _remote_guard:
        return {k: {"fetched_at": v["fetched_at"], "fail_count": v["fail_count"],
                    "next_retry_at": v["next_retry_at"], "inflight": v["inflight"],
                    "has_entry": v["entry"] is not None}
                for k, v in _remote_state.items()}
