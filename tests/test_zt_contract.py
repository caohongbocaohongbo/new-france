"""涨停基础/增强拆分（§12.4 第4步）离线单测。"""
import pandas as pd

from backend.agents.layer1_data_collector.sources.zt_contract import (
    classify_zt_basic, compute_limit_prices, enrich_zt_events, required_events_available,
)


def test_compute_limit_prices_main_board_rounding():
    # 低价股 1.03 涨停价 1.13（涨幅 9.7087%），不得被 9.8% 近似漏判
    up, down, ver = compute_limit_prices("1.03", "600001", "测试", date(2026, 7, 6) if False else None)
    assert up == 1.13
    assert down == 0.93


def test_compute_limit_prices_gem_star():
    up, down, _ = compute_limit_prices("10.00", "300001", "创业", None)
    assert up == 12.0 and down == 8.0


def test_compute_limit_prices_st_rules_by_date():
    from datetime import date as _date
    # v2（2026-06-30 后）主板 ST 10%
    up, _, ver = compute_limit_prices("10.00", "600001", "ST股", _date(2026, 7, 6))
    assert up == 11.0 and ver == "v2"
    # v1（生效前）主板 ST 5%
    up, _, ver = compute_limit_prices("10.00", "600001", "ST股", _date(2026, 6, 1))
    assert up == 10.5 and ver == "v1"


def test_classify_zt_basic_touched_vs_at_limit():
    row = {"代码": "600001", "名称": "测试", "最新价": 10.9, "最高价": 11.0, "涨停价": 11.0}
    assert classify_zt_basic(row)["state"] == "touched"
    row["最新价"] = 11.0
    assert classify_zt_basic(row)["state"] == "at_limit"


def test_classify_zt_basic_unknown_limit():
    row = {"代码": "600001", "名称": "测试", "最新价": 10.9, "最高价": 11.0, "涨停价": None, "昨收": None}
    out = classify_zt_basic(row)
    assert out["state"] == "unknown"
    assert "limit_price_unknown" in out["unknown_reasons"]


def test_enrich_and_required_events():
    zt = pd.DataFrame([{"代码": "600001", "封板时间": 143000, "炸板次数": 0, "连板数": 2}])
    events = enrich_zt_events(zt, ["600001", "600002"])
    assert events["600001"]["available"] is True and events["600001"]["break_count"] == 0
    assert events["600002"] is None
    missing = required_events_available(events)
    assert missing == ["600002"]

def test_resolve_zt_basic_from_quotes():
    import pandas as pd
    from backend.agents.layer1_data_collector.sources.zt_contract import resolve_zt_basic_from_quotes

    quotes = pd.DataFrame([
        {"代码": "600001", "名称": "测试", "最新价": 11.0, "最高价": 11.0, "涨停价": 11.0, "昨收": 10.0},
        {"代码": "600002", "名称": "普通", "最新价": 10.0, "最高价": 10.2, "涨停价": 11.0, "昨收": 10.0},
    ])
    out = resolve_zt_basic_from_quotes(quotes)
    assert out[out["代码"] == "600001"]["zt_basic_state"].tolist() == ["at_limit"]
    assert out[out["代码"] == "600002"]["zt_basic_state"].tolist() == ["none"]
