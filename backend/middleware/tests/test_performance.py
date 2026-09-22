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
