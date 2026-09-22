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
