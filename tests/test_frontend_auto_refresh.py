"""Phase 1 前端自动刷新静态断言。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_auto_refresh_present():
    js = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
    html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    css = (ROOT / "frontend/css/styles.css").read_text(encoding="utf-8")
    assert "startAutoRefresh" in js
    assert "AUTO_REFRESH_INTERVAL" in js
    assert "navigateTo(_currentPage, false, true)" in js
    assert "resetAutoRefreshCountdown" in js
    assert "autoRefreshStatus" in html
    assert ".auto-refresh" in css
    # Phase 3 SSE
    assert "initSSE" in js
    assert "EventSource" in js
