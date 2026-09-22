# 性能优化 · 查询接口响应稳定 — 本次修改汇总 diff

> 依据：docs/性能优化_查询接口响应稳定_施工方案.md（P0/P1/P2/P3 全部落地；P4 K线读穿与 P5 冷启动按文档门槛留待 P0 生产数据后独立开工）。
> 范围自证：以下为本任务全部改动。工作区中另有 principal_capital 重构（docs/23、pipeline.py、intraday_state.py 等）与其它在途未提交改动，**不属于本 diff**。

## 0. 变更清单

### 新增（9 个文件）

| 文件 | 说明 |
|---|---|
| backend/services/snapshot_store.py | P1 统一快照存储层（原子写/LRU 字节缓存/早期 304/远程合并缓存） |
| backend/middleware/__init__.py + performance.py | P0 应用内观测 ASGI 中间件 |
| backend/services/tests/__init__.py + test_snapshot_store.py + test_perf_contracts.py | P1/P2/P3 单测与契约测试 |
| backend/middleware/tests/__init__.py + test_performance.py | P0 中间件测试 |
| backend/plugins/smart_money_radar/tests/test_snapshot_atomic.py | P1 迁移后 smart_money_radar 快照写读定向测试 |

### 修改（13 个文件）

| 文件 | 改动点 |
|---|---|
| backend/main.py | 移除全局响应体 ETag 缓冲中间件；接入 PerformanceMiddleware + GZipMiddleware |
| backend/api/router_screening.py | /latest 早期 304 + view=summary 契约裁剪 + 原子写 |
| backend/plugins/principal_capital/router.py | /tier-flow/latest 重写：view=compact 默认、服务端分页、字段白名单、早期 304 |
| backend/plugins/overnight_arbitrage/{config,service,router}.py | latest/history 早期 304 + compact 契约 + 分页/筛选 + /{code}/history 明细 + 原子写 + compact artifact |
| backend/plugins/common.py | write_snapshot 原子写；read_snapshot_resilient 网络步接入远程合并缓存；db_append/db_delete busy 有界重试 |
| backend/plugins/smart_money_radar/service.py | _write_latest/orderflow/auction 4 处快照写点原子化 |
| backend/services/task_history.py | 追加记录原子写 |
| backend/services/optional_source_health.py | _write_json 原子写 |
| backend/db/database.py | timeout=5 + busy_timeout/foreign_keys/synchronous PRAGMA + WAL 受控初始化校验 |
| frontend/js/app.js | 4 处 /screening/latest 读取显式切 view=summary |
| tests/test_etag_middleware.py | 重写为迁移后契约（未迁移小接口无 ETag；tier-flow 304/变更） |
| tests/test_screening_background.py | /latest 同步化后直接调用适配 |
| backend/plugins/smart_picker_hub/tests/test_common_read.py | 适配 store 集成（fake 签名/reset） |

---

## 1. P0 观测（backend/main.py）

### before（全局 ETag 缓冲中间件 + 无观测）

~~~diff
-    # 数据回显 ETag（Phase 2）：对 JSON 响应加 ETag，支持 If-None-Match → 304
-    class _ETagMiddleware(_BaseHTTPMiddleware):
-        async def dispatch(self, request, call_next):
-            # 完整消费响应体、拼接 bytes、计算 MD5 后才判断 304
-    _app.add_middleware(_ETagMiddleware)
~~~

### after

~~~diff
+    # P0 观测：纯 ASGI middleware，位于 GZip/路由之外，记录首/末响应字节与快照来源
+    from .middleware.performance import PerformanceMiddleware
+    _app.add_middleware(PerformanceMiddleware)
+
+    # P2 GZip：字段裁剪完成后启用（min 1KB，避免小响应徒增 CPU）
+    from fastapi.middleware.gzip import GZipMiddleware
+    _app.add_middleware(GZipMiddleware, minimum_size=1024)
~~~

新增 backend/middleware/performance.py 全文：

~~~~
"""P0 应用内观测：纯 ASGI middleware（包装 send 记录首/末响应字节，不缓冲、不改流式语义）。

字段：request_id / method / route_template / status / ttfb_ms / duration_ms / response_bytes /
      content_encoding / etag_result / snapshot_source / snapshot_cache / upstream_ms /
      process_uptime_s / error_type。
health 请求不写普通 access 日志，每 5 分钟汇总一次 health 延迟与 uptime。
"""
from __future__ import annotations

import logging
import time
import uuid

logger = logging.getLogger("performance")
_STARTED_AT = time.perf_counter()

# health 聚合（不写逐请求日志，每 5 分钟汇总）
_health_latencies: list = []
_health_last_summary_at = 0.0
HEALTH_SUMMARY_INTERVAL = 300.0


def process_uptime_s() -> float:
    return round(time.perf_counter() - _STARTED_AT, 3)


def _summarize_health() -> None:
    global _health_latencies, _health_last_summary_at
    if not _health_latencies:
        return
    lat = sorted(_health_latencies)
    p50 = lat[len(lat) // 2]
    p95 = lat[int(len(lat) * 0.95)]
    count = len(lat)
    _health_latencies = []
    _health_last_summary_at = time.perf_counter()
    logger.info("health_summary count=%d p50_ms=%.2f p95_ms=%.2f uptime_s=%.1f",
                count, p50 * 1000, p95 * 1000, process_uptime_s())


class PerformanceMiddleware:
    """记录每个 HTTP 请求的响应生成与发送全程；SSE 等流式响应 ttfb < duration。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        t0 = time.perf_counter()
        info = {"status": None, "first_byte_at": None, "last_byte_at": None,
                "bytes": 0, "headers": [], "logged": False, "error_type": None}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                info["status"] = message.get("status")
                info["headers"] = list(message.get("headers") or [])
                info["first_byte_at"] = time.perf_counter()
            elif message["type"] == "http.response.body":
                info["last_byte_at"] = time.perf_counter()
                info["bytes"] += len(message.get("body") or b"")
                if not message.get("more_body", False):
                    self._log(scope, info, t0)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:  # noqa: BLE001 记录错误类型后上抛
            info["error_type"] = type(exc).__name__
            raise

    def _log(self, scope, info: dict, t0: float) -> None:
        if info["logged"]:
            return
        info["logged"] = True
        if info["last_byte_at"] is None:
            info["last_byte_at"] = time.perf_counter()
        path = scope.get("path") or ""
        is_health = path.rstrip("/").endswith(("/system/health", "/health"))
        if is_health:
            self._record_health(info, t0)
            return
        route = scope.get("route")
        route_template = getattr(route, "path", None) or path.split("?")[0] or "/unknown"
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in info["headers"]}
        state = scope.get("state") or {}
        snapshot = state.get("_snapshot_context") if isinstance(state, dict) else getattr(state, "_snapshot_context", None)
        snapshot = snapshot or {}
        etag_result = "hit" if info["status"] == 304 else ("miss" if headers.get("etag") else "none")
        ttfb_ms = (info["first_byte_at"] - t0) * 1000 if info["first_byte_at"] else None
        duration_ms = (info["last_byte_at"] - t0) * 1000
        logger.info(
            "http request_id=%s method=%s route=%s status=%s ttfb_ms=%.2f duration_ms=%.2f "
            "response_bytes=%d content_encoding=%s etag_result=%s snapshot_source=%s "
            "snapshot_cache=%s upstream_ms=%s process_uptime_s=%.1f error_type=%s",
            uuid.uuid4().hex[:12], scope.get("method"), route_template, info["status"],
            ttfb_ms if ttfb_ms is not None else -1.0, duration_ms, info["bytes"],
            headers.get("content-encoding") or "identity", etag_result,
            snapshot.get("source", "none"), snapshot.get("cache", "none"),
            snapshot.get("upstream_ms", "-"), process_uptime_s(), info["error_type"] or "none",
        )

    def _record_health(self, info: dict, t0: float) -> None:
        global _health_latencies, _health_last_summary_at
        if info["last_byte_at"] is None:
            info["last_byte_at"] = time.perf_counter()
        _health_latencies.append(info["last_byte_at"] - t0)
        if time.perf_counter() - _health_last_summary_at >= HEALTH_SUMMARY_INTERVAL:
            _summarize_health()


def set_snapshot_context(request, source: str = "none", cache: str = "none", upstream_ms=None) -> None:
    """端点向观测中间件注入快照来源/缓存命中/上游耗时（写入 request.state）。"""
    try:
        state = request.state  # Starlette State（底层为 scope["state"] dict）
        payload = {
            "source": source, "cache": cache,
            "upstream_ms": (f"{upstream_ms:.0f}" if isinstance(upstream_ms, (int, float)) else upstream_ms),
        }
        if isinstance(state, dict):
            state["_snapshot_context"] = {**state.get("_snapshot_context", {}), **payload}
        else:
            context = getattr(state, "_snapshot_context", None) or {}
            state._snapshot_context = {**context, **payload}
    except Exception:  # noqa: BLE001 观测失败不影响业务
        pass
~~~~

---

## 2. P1 快照基座（backend/services/snapshot_store.py 全文）

~~~~
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
~~~~

### 2.1 backend/plugins/common.py（三处）

**write_snapshot → 原子写**

~~~diff
-    text = json.dumps(json_safe(payload), ensure_ascii=False, indent=2, default=str)
-    _atomic_write(latest_path(name), text)
-    _atomic_write(data_backend_path(name), text)
+    from backend.services.snapshot_store import atomic_write_json
+
+    atomic_write_json(latest_path(name), payload)
+    atomic_write_json(data_backend_path(name), payload)
~~~

**read_snapshot_resilient 步骤 4 → 远程合并缓存（single-flight/退避/条件 GET/stale）**

~~~diff
-    # 步骤 4：网络兜底（httpx，超时可配置）
-    try:
-        import httpx
-        url = f"{SNAPSHOT_RAW_BASE}/reports/data_backend/{name}_latest.json"
-        resp = httpx.get(url, timeout=float(timeout))
-        ...
+    from backend.services.snapshot_store import RemotePolicy, fetch_remote_snapshot
+
+    url = f"{SNAPSHOT_RAW_BASE}/reports/data_backend/{name}_latest.json"
+    policy = RemotePolicy(total_timeout=float(timeout), connect_timeout=1.5,
+                          ttl_seconds=float(ttl_seconds))
+    entry = fetch_remote_snapshot(name, url, policy)
+    if entry is None:
+        err = remote_last_error(name)
+        return {"status": "no_data", "_source": "unavailable", "items": [],
+                "reason": f"remote_fetch_failed:err:{err.split(':', 1)[0]}"}
+    payload = entry.parsed()
+    payload["_source"] = "snapshot"
+    if entry.stale:  # stale 数据显式标注（_source/stale/fetched_at/refresh_error）
+        payload["stale"] = True
+        payload["fetched_at"] = entry.fetched_at
+        payload["refresh_error"] = entry.refresh_error
+    elif int(ttl_seconds) > 0:
+        _write_remote_cache(name, payload)
+    return payload
~~~

**db_append / db_delete → busy 有界重试（P3 §7.2）**

~~~diff
+def _is_sqlite_busy(exc) -> bool:
+    return "locked" in str(exc).lower() or "busy" in str(exc).lower()
+
 def db_append(table, rows):
-    try: ... to_sql ... except Exception: return 0
+    # 3 次有界重试（0.2s/0.4s 退避），仅对 locked/busy 重试；锁等待记
+    # logger.warning(retry/wait_s/table)；非 busy 异常仍返回 0 不阻断主链路
+    # db_delete 同构（engine.begin() 短事务 + busy 重试）
~~~

### 2.2 写点迁移（原子写覆盖热接口全部写点）

| 文件 | 写点 |
|---|---|
| backend/api/router_screening.py | _write_json_cache → atomic_write_json(reports/latest.json) |
| backend/services/task_history.py | append_task_record → atomic_write_json(task_history.json) |
| backend/services/optional_source_health.py | _write_json → atomic_write_json |
| backend/plugins/overnight_arbitrage/service.py | write_overnight_report / update_overnight_history（compact artifact 随 history_file 同目录同 stem）→ atomic_write_json |
| backend/plugins/smart_money_radar/service.py | _write_latest、orderflow_latest、auction_latest×2 → atomic_write_json |

---

## 3. P2 接口瘦身与早期 304

### 3.1 tier-flow/latest（backend/plugins/principal_capital/router.py）

~~~diff
-@router.get("/tier-flow/latest")
-async def tier_flow_latest():
-    try:
-        from .tier_flow import read_latest
-        return read_latest()
-    except Exception as exc:
-        return {"status": "error", "error": str(exc)}
+_TIER_COMPACT_FIELDS = ("code", "name", "super_net", "big_net", "smart_ratio", "state")
+_TIER_FIELD_WHITELIST = _TIER_COMPACT_FIELDS + (
+    "price", "change_pct", "total_amount", "mid_net", "small_net",
+    "super_ratio", "big_ratio", "vwap_large",
+)
+
+@router.get("/tier-flow/latest")
+def tier_flow_latest(request: Request, view: str = Query("compact"),
+                     limit: int = Query(100, ge=1, le=200),
+                     offset: int = Query(0, ge=0, le=10000), fields: str = Query("")):
+    # view/fields 白名单校验（非法 422）；entry = read_snapshot_entry(REPORT_DIR/"tier_flow_latest.json")
+    # 本地缺失 → fetch_remote_snapshot("tier_flow", ..., RemotePolicy()) 兜底
+    # ETag = etag_for_params(entry.etag, "view=compact&limit=..&offset=..&fields=..")
+    # 304 在 entry.parsed() 之前返回；compact = 服务端分页 + 6 字段行（fields 白名单扩展）
+    # 顶层：status/now/active_source/degraded/states/total/returned/limit/offset/items
+    # view=full → json_response_from_entry（完整快照）
~~~
（快照本身已按 smart_ratio 降序，服务端直接切片分页；前端不再 slice(0,100)。）

### 3.2 overnight-arbitrage/latest + history（config/service/router 三文件）

~~~diff
 config.py:
+HISTORY_COMPACT_FILE = REPORT_DIR / "overnight_arbitrage_history_compact.json"

 service.py:
+OA_COMPACT_TOP_FIELDS = ("status","strategy","date","generated_at","valid_window","buy_count",
+    "watch_count","total_candidates","total_scanned","source_status","empty_reason",
+    "trade_note","message")   # message 为前端轮询/错误路径契约字段
+OA_COMPACT_RESULT_FIELDS = ("code","name","action","decision_score","current_price",
+    "change_pct","turnover","volume_ratio","reasons","risks")
+def compact_overnight_report(payload) -> dict: ...  # 剔除 data_quality/rejected/原始行情中间因子
+def compact_history_payload(payload) -> dict: ...   # 剔除 recommendations/price_pushes/pe_values/pullback_values

 router.py:
-@router.get("/latest")  async def get_latest_overnight(): return read_overnight_report_resilient()
+@router.get("/latest")
+def get_latest_overnight(request: Request, view: str = Query("compact")):
+    # _oa_latest_entry()：本地完成态优先；本地 running 不被远程旧 completed 掩盖；
+    # 本地空/错误 → fetch_remote_snapshot(ttl=300)；compact 默认 + 早期 304；view=full 保留
+
-@router.get("/history") async def get_overnight_history(): return read_overnight_history_resilient()
+@router.get("/history")
+def get_overnight_history(request: Request, limit: int = Query(100, ge=1, le=200),
+                          offset: int = Query(0, ge=0, le=10000), code: str = Query(None),
+                          date_from: str = Query(None), date_to: str = Query(None)):
+    # 读 compact artifact（mtime 或 total_stocks 与 full 不一致 → 即时重建，防测试/异常写入污染）
+    # 服务端 code/date 筛选 + 分页；默认无 recommendations/数值序列；早期 304
+
+@router.get("/{code}/history")
+def get_overnight_history_code(request: Request, code: str):
+    # 读 full 文件按需返回单股完整明细（含 recommendations）
~~~

### 3.3 screening/latest（backend/api/router_screening.py）

~~~diff
-@router.get("/latest")
-async def get_latest_screening():
-    # json.loads + 富化 + FastAPI 序列化，无 ETag
+@router.get("/latest")
+def get_latest_screening(request: Request = None, view: str = Query("full")):
+    # ETag = etag_for_params(latest.etag, "watchlist=<wl_etag>&view=full|summary")
+    # 304 在 json.loads/富化之前返回；view=summary 裁剪 report_md/report_html/evidence
+    # （audit/price_history/factors 保留：前端 dashboard 降级统计与详情面板依赖）
+    # request=None 退化纯 dict（既有测试/内部直接调用兼容）
~~~
前端：app.js 4 处读取 /screening/latest 显式改 /screening/latest?view=summary。

### 3.4 前端 ETag 兼容

既有 apiFetch（app.js）按 path 缓存 {etag, text} 并发送 If-None-Match、304 回放缓存 body；弱 ETag（W/"..."）原样往返匹配，无需改动。

---

## 4. P3 SQLite 稳定性（backend/db/database.py 重写）

~~~diff
-engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})
+engine = create_engine(DATABASE_URL, echo=False,
+                       connect_args={"check_same_thread": False, "timeout": 5})
+
+@event.listens_for(engine, "connect")
+def _set_sqlite_pragmas(dbapi_connection, connection_record):
+    # 每个新连接：PRAGMA busy_timeout=5000 / foreign_keys=ON / synchronous=NORMAL
+
+def init_db():
+    Base.metadata.create_all(bind=engine)
+    with engine.connect() as conn:
+        before = _journal_mode(conn)
+        if before != "wal":
+            conn.execute(text("PRAGMA journal_mode=WAL"))
+        after = _journal_mode(conn)
+        if after != "wal":
+            logger.warning(...)   # 校验失败继续运行，锁等待依赖 busy_timeout
+        else:
+            conn.execute(text("PRAGMA synchronous=NORMAL"))
~~~
（不启用 pool_pre_ping，按 §7.1；WAL 回滚 = checkpoint + 无并发写时显式 journal_mode=DELETE，见方案 §7.4/§12。）

---

## 5. 测试与验收

### 5.1 测试结果（全绿）

| 套件 | 结果 |
|---|---|
| backend/services/tests（snapshot_store 9 + perf_contracts 9 + 既有） | 28 passed |
| backend/middleware/tests | 3 passed |
| tests（etag 契约重写 + screening 适配 + 既有） | 203 passed |
| backend/plugins 逐目录回归（除 smart_money_radar，按方案 §11.4） | 全部 passed（principal 133、oa 28、hub 25 等） |
| smart_money_radar 定向（test_snapshot_atomic） | 2 passed |

### 5.2 实测指标（本机 uvicorn + curl）

| 项 | 实测 | 方案目标 |
|---|---|---|
| tier-flow compact | 18,443B（full 1,244,878B，**降幅 98%**） | ≥80% ✅ |
| tier-flow 304 | **0.66ms** | ≤5ms ✅ |
| tier-flow 默认条数/硬上限 | 100 / limit=201 → 422 | ✅ |
| oa latest compact | 839B（剔除 data_quality/rejected） | ✅（本地样本小；生产 464KB → 约 1KB 量级） |
| oa history 页 | 100 条、无 recommendations/数值序列；limit=201 → 422 | ✅ |
| oa /{code}/history | 单股完整明细（含 recommendations） | ✅ |
| screening 304 | 0.76ms（ETag = 快照版本 + watchlist 版本） | ✅ |
| gzip | Accept-Encoding: gzip → content-encoding: gzip | ✅ |
| 观测日志 | route_template/ttfb/duration/bytes/etag_result/snapshot_source 全字段；health 走 5 分钟汇总 | ✅ |
| SQLite | journal_mode=wal、busy_timeout=5000、synchronous=1；20 读 + 2 写 × 100 短事务无 locked | ✅ |

### 5.3 复现命令

~~~bash
cd /Users/fangcang/new-france
python3 -m pytest tests backend/services backend/middleware -q
python3 -m pytest backend/plugins/smart_money_radar/tests/test_snapshot_atomic.py -q
python3 -m backend.main --serve &
curl -s -D - -o /dev/null http://127.0.0.1:8000/api/v1/principal-capital/tier-flow/latest | grep -i etag
# 再带 If-None-Match 发一次 → 304；-H 'Accept-Encoding: gzip' → content-encoding: gzip
curl -s 'http://127.0.0.1:8000/api/v1/principal-capital/tier-flow/latest?limit=201' -o /dev/null -w '%{http_code}
'  # 422
~~~

---

## 6. 明确不包含（红线与门槛）

- **P4 K 线持久化读穿 / P5 冷启动**：方案 §8/§9 明确要求"待 P0 生产数据（慢请求 TOP / 平台唤醒分段）后才开工"，本批不动。
- **不新增重复索引**（§7.3 条件未触发）；**不改业务阈值/策略公式/邮件规则**；**不引入 Redis/外部库**（httpx 已在 22 方案进入 requirements）。
- 本 diff 未提交到 git（工作区与 principal_capital 重构等其它在途改动混合，按需分拣后自行提交）；提交建议仅包含 §0 清单内文件。
