"""22 聚合编排服务单测（全 mock：不联网、不落盘、不动真实快照）。"""
import asyncio
from datetime import date

import pandas as pd
import pytest

import backend.plugins.smart_picker_hub.service as service


TECH_ITEMS = [
    {"code": "600001", "name": "测试股份", "price": 10.0, "is_intraday": 0, "tech_score": 70.0,
     "macd_golden": 1, "macd_dif": -0.12, "macd_dea": -0.10, "kdj_k": 15.0, "kdj_d": 20.0, "rsi": 25.0,
     "boll_mb": 9.5, "multi_hit": 1},
    {"code": "600002", "name": "次票", "price": 9.0, "is_intraday": 0, "tech_score": 60.0, "kdj_golden": 1},
]
TREND_ITEMS = [
    {"code": "600001", "name": "测试股份", "price": 10.1, "is_intraday": 0, "ma_aligned": 1, "new_high": 1,
     "trend_score": 80.0, "trend_score_pct": 90.0},
    {"code": "600003", "name": "趋势票", "price": 12.0, "is_intraday": 0, "ma_aligned": 1, "new_high": 1,
     "trend_score": 75.0, "trend_score_pct": 80.0},
]
PATTERN_ITEMS = [{"code": "600001", "name": "测试股份", "price": 10.2, "is_intraday": 0, "platform_break": 1,
                  "pattern_score": 75.0, "pattern_score_pct": 60.0, "platform_high": 9.8, "volume_ratio": 2.1}]

SNAPS = {
    "tech_indicators": {"status": "completed", "date": "2026-09-08", "items": TECH_ITEMS},
    "trend_strength": {"status": "completed", "date": "2026-09-08", "items": TREND_ITEMS,
                       "strong_pool": TREND_ITEMS},  # pool 重复行 → 去重
    "pattern_scanner": {"status": "completed", "date": "2026-09-08", "items": PATTERN_ITEMS},
    "chip_scanner": {"status": "no_data", "local_only": True, "date": None},
}


@pytest.fixture
def mocked(monkeypatch, tmp_path):
    def fake_resilient(name, **kwargs):
        return dict(SNAPS.get(name) or {"status": "empty", "items": []})

    monkeypatch.setattr(service, "REPORT_DIR", tmp_path)  # .hub.lock 写入临时目录
    monkeypatch.setattr(service, "read_snapshot_resilient", fake_resilient)
    monkeypatch.setattr(service, "read_snapshot", lambda name: {})
    monkeypatch.setattr(service, "write_snapshot", lambda name, payload: payload)
    monkeypatch.setattr(service, "is_trading_day", lambda d: True)
    monkeypatch.setattr(service, "load_zt_codes", lambda: {"600002"})
    monkeypatch.setattr(service, "load_badge_sources",
                        lambda: {"fund_flow": {}, "tier_state": {}, "radar": {}})
    monkeypatch.setattr(service, "push_multi_hit", lambda *a, **k: (False, set(), None))
    monkeypatch.setattr(service, "db_append", lambda table, rows: len(rows))
    monkeypatch.setattr(service, "db_delete", lambda table, where: 0)
    monkeypatch.setattr(service, "db_query", lambda sql, params=None: pd.DataFrame())
    captured = {}

    async def fake_charts(items, cfg, kf=None):
        captured["chart_codes"] = [it["code"] for it in items[: int(cfg["chart_precompute_n"])]]
        return captured["chart_codes"], {}

    monkeypatch.setattr(service, "precompute_charts", fake_charts)
    monkeypatch.setattr(service, "refresh_perf",
                        lambda items, target, cfg, kf=None: {"tracked": len(items), "filled": 0, "missing": 0})
    return captured


def test_hub_run_full(mocked):
    payload = asyncio.run(service.run_smart_picker_hub_once(force=True, target_date="2026-09-08"))
    assert payload["status"] == "degraded"  # chip 不可用 → 降级但出结果
    assert payload["date"] == "2026-09-08"
    assert payload["data_age_days"] == 0
    assert payload["zt_gate_applied"] is True
    assert payload["strategies"]["chip"]["available"] is False
    assert payload["strategies"]["chip"]["reason"] == "no_data"
    # 权重：chip 剔除后归一化（0.30/0.25/0.20 → 0.4/0.333333/0.266667）
    assert abs(sum(payload["weights"].values()) - 1.0) < 1e-6
    assert set(payload["weights"]) == {"tech", "trend", "pattern"}
    codes = [it["code"] for it in payload["items"]]
    assert codes == ["600001", "600003"]  # 600002 被涨停门控剔除；hub_score 降序
    top = payload["items"][0]
    assert top["code"] == "600001"
    assert top["hit_strategies"] == 3
    assert top["resonance"] == 1
    assert abs(top["hub_score"] - 86.0) < 1e-6  # 0.4*100 + 0.333333*90 + 0.266667*60
    assert top["hub_score_pct"] == 100.0
    assert top["hits"]["tech"]["explain"]  # 命中解释已生成
    assert payload["signal_counts"]["total"] == 2
    assert payload["signal_counts"]["resonance"] == 1
    assert payload["signal_counts"]["by_strategy"] == {"tech": 1, "trend": 2, "pattern": 1, "chip": 0}
    assert mocked["chart_codes"] == ["600001", "600003"]
    assert payload["charts_precomputed"]["n"] == 40


def test_hub_run_all_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(service, "read_snapshot_resilient", lambda name, **k: {"status": "empty", "items": []})
    monkeypatch.setattr(service, "write_snapshot", lambda name, payload: payload)
    monkeypatch.setattr(service, "is_trading_day", lambda d: True)
    payload = asyncio.run(service.run_smart_picker_hub_once(force=True, target_date="2026-09-08"))
    assert payload["status"] == "no_data"
    assert payload["reason"] == "all_strategies_unavailable"
    assert payload["items"] == []  # 不伪造


def test_refresh_perf_no_lookahead(monkeypatch):
    rows = [{"signal_date": "2026-09-03", "code": "600001", "strategies": "tech,trend",
             "hub_score": 80.0, "close_entry": 10.0,
             "t1_filled": 0, "t3_filled": 0, "t5_filled": 0}]
    monkeypatch.setattr(service, "db_query",
                        lambda sql, params=None: pd.DataFrame(rows) if "picker_perf_daily" in sql else pd.DataFrame())
    appended, deleted = [], []
    monkeypatch.setattr(service, "db_append", lambda table, r: appended.extend(r) or len(r))
    monkeypatch.setattr(service, "db_delete", lambda table, where: deleted.append(where) or 0)

    def fake_kline(code, days):
        dates = ["2026-09-02", "2026-09-03", "2026-09-04", "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10"]
        closes = [10.0, 10.0, 10.2, 10.1, 10.3, 10.4, 10.5]
        return pd.DataFrame({"日期": dates, "收盘": closes})

    target = date(2026, 9, 8)
    result = service.refresh_perf([], target,
                                  {"perf_windows": [1, 3, 5], "perf_track_top_n": 60, "perf_lookback_days": 20},
                                  fake_kline)
    assert result["filled"] == 2  # t1(09-04) + t3(09-08)
    updated = [r for r in appended if isinstance(r, dict)]
    assert updated, "应有回填更新行"
    row = updated[0]
    assert row["t1_filled"] == 1 and abs(row["t1_ret"] - 0.02) < 1e-9
    assert row["t3_filled"] == 1 and abs(row["t3_ret"] - 0.03) < 1e-9
    assert row["t5_filled"] == 0  # t+5=09-10 未到期 → 即便 bar 存在也不填（无前视）
    assert row.get("t5_ret") is None
    assert deleted == [{"signal_date": "2026-09-03", "code": "600001"}]


def test_hub_lock_held_skips(monkeypatch, tmp_path):
    """DIFF-4：另一进程持有 .hub.lock → 本轮跳过，不写快照。"""
    monkeypatch.setattr(service, "REPORT_DIR", tmp_path)
    written = []
    monkeypatch.setattr(service, "write_snapshot", lambda name, payload: written.append(name) or payload)
    monkeypatch.setattr(service, "is_trading_day", lambda d: True)
    monkeypatch.setattr(service, "load_strategy_rows", lambda: ({}, {k: [] for k in service.STRATEGY_KEYS}))
    (tmp_path / ".hub.lock").write_text("2026-09-08T15:00:00+08:00")
    payload = asyncio.run(service.run_smart_picker_hub_once(force=True, target_date="2026-09-08"))
    assert payload["status"] == "skipped" and payload["reason"] == "hub_lock_held"
    assert not written  # 不覆盖现有快照


def test_hub_lock_released_after_run(mocked, tmp_path):
    """正常跑完后 .hub.lock 必须被清理。"""
    asyncio.run(service.run_smart_picker_hub_once(force=True, target_date="2026-09-08"))
    assert not (tmp_path / ".hub.lock").exists()
