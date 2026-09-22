"""P0 观测中间件单测：慢端点/流式/health 摘要/route_template。"""
import time

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

import backend.middleware.performance as perf
from backend.middleware.performance import PerformanceMiddleware


def _make_app():
    app = FastAPI()
    app.add_middleware(PerformanceMiddleware)

    @app.get("/api/v1/slow")
    async def slow():
        time.sleep(0.15)
        return {"ok": True}

    @app.get("/api/v1/stream")
    async def stream():
        async def gen():
            yield "chunk1"
            await __import__("asyncio").sleep(0.1)
            yield "chunk2"

        return StreamingResponse(gen(), media_type="text/plain")

    @app.get("/api/v1/items/{item_id}")
    async def item(item_id: int):
        return {"id": item_id}

    @app.get("/api/v1/system/health")
    async def health():
        return {"ok": True}

    return app


def test_slow_endpoint_duration_includes_generation(caplog):
    caplog.set_level("INFO", logger="performance")
    client = TestClient(_make_app())
    client.get("/api/v1/slow")
    line = next(l for l in caplog.messages if "request_id=" in l)
    assert "duration_ms=" in line and "ttfb_ms=" in line
    assert "route=/api/v1/slow" in line
    duration = float(line.split("duration_ms=")[1].split(" ")[0])
    assert duration >= 150  # 慢端点耗时被完整记录


def test_streaming_ttfb_less_than_duration(caplog):
    caplog.set_level("INFO", logger="performance")
    client = TestClient(_make_app())
    client.get("/api/v1/stream")
    line = next(l for l in caplog.messages if "request_id=" in l and "stream" in l)
    ttfb = float(line.split("ttfb_ms=")[1].split(" ")[0])
    duration = float(line.split("duration_ms=")[1].split(" ")[0])
    assert 0 < ttfb < duration  # 不缓冲流式响应


def test_route_template_and_health_summary(caplog, monkeypatch):
    caplog.set_level("INFO", logger="performance")
    monkeypatch.setattr(perf, "HEALTH_SUMMARY_INTERVAL", 0.0)
    client = TestClient(_make_app())
    client.get("/api/v1/items/42")
    client.get("/api/v1/system/health")
    client.get("/api/v1/system/health")
    assert any("route=/api/v1/items/{item_id}" in m for m in caplog.messages)
    assert any("health_summary" in m for m in caplog.messages)  # health 走 5 分钟汇总
    assert not any("request_id=" in m and "system/health" in m for m in caplog.messages)  # health 不写普通 access



def _make_gzip_app():
    """Performance 为最外层（后添加），位于 GZip 外 → 记录压缩后编码与字节。"""
    from fastapi.middleware.gzip import GZipMiddleware

    app = FastAPI()
    app.add_middleware(GZipMiddleware, minimum_size=1)
    app.add_middleware(PerformanceMiddleware)

    @app.get("/api/v1/big")
    async def big():
        return {"payload": "x" * 5000}

    @app.get("/api/v1/boom")
    async def boom():
        raise ValueError("boom")

    return app


def test_gzip_outer_observation(caplog):
    """P0-1 验收：gzip 请求日志 content_encoding=gzip、字节数为压缩后字节。"""
    caplog.set_level("INFO", logger="performance")
    client = TestClient(_make_gzip_app())
    resp = client.get("/api/v1/big", headers={"Accept-Encoding": "gzip"})
    assert resp.headers.get("content-encoding") == "gzip"
    line = next(l for l in caplog.messages if "request_id=" in l and "big" in l)
    assert "content_encoding=gzip" in line
    logged_bytes = int(line.split("response_bytes=")[1].split(" ")[0])
    assert logged_bytes < 5000  # 压缩后字节数 < 原始 5000


def test_exception_path_logs_error_type(caplog):
    """P0-1 验收：异常请求必须记录一次日志且 error_type 非 none。"""
    caplog.set_level("INFO", logger="performance")
    client = TestClient(_make_gzip_app(), raise_server_exceptions=False)
    client.get("/api/v1/boom")
    line = next((l for l in caplog.messages if "request_id=" in l and "boom" in l), None)
    assert line is not None
    assert "error_type=ValueError" in line


def test_x_request_id_header_present():
    client = TestClient(_make_gzip_app(), raise_server_exceptions=False)
    resp = client.get("/api/v1/big")
    assert resp.headers.get("x-request-id")

