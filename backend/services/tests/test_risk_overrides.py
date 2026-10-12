"""26 功能1 风险否决层单测。"""
import pytest

from backend.services.risk_overrides import (
    LEVEL_ORDER,
    apply_risk_overrides,
    build_stock_ctx,
    downgrade,
    min_rank,
)


def test_downgrade_order():
    assert downgrade("STRONG_BUY") == "BUY"
    assert downgrade("BUY") == "WATCH"
    assert downgrade("WATCH") == "HOLD"
    assert downgrade("HOLD") == "HOLD"


def test_r1_chase_high_downgrades():
    ctx = {"pct_change_5d": 18.0}
    res = apply_risk_overrides(ctx, "BUY")
    assert "R1" in res.hits
    assert res.level == "WATCH"
    assert res.confidence_multiplier < 1.0


def test_r2_overbought_downgrades():
    ctx = {"rsi14": 75.0, "near_high_20d": True}
    res = apply_risk_overrides(ctx, "STRONG_BUY")
    assert "R2" in res.hits
    assert res.level == "BUY"


def test_r3_break_count_downgrades():
    ctx = {"break_count": 10}
    res = apply_risk_overrides(ctx, "BUY")
    assert "R3" in res.hits
    assert res.level == "WATCH"


def test_r4_low_float_cap_caps_watch():
    ctx = {"float_market_cap_yi": 10.0}
    res = apply_risk_overrides(ctx, "STRONG_BUY")
    assert "R4" in res.hits
    assert res.level == "WATCH"


def test_r5_risk_off_lowers_confidence_but_keeps_level():
    ctx = {"index_gain": -3.0}
    res = apply_risk_overrides(ctx, "BUY")
    assert "R5" in res.hits
    assert res.level == "BUY"  # 不降级
    assert res.confidence_multiplier < 1.0


def test_r6_pre_earnings_downgrades():
    ctx = {"days_to_earnings": 2}
    res = apply_risk_overrides(ctx, "BUY")
    assert "R6" in res.hits
    assert res.level == "WATCH"


def test_r7_crisis_news_caps_watch():
    ctx = {"has_crisis_news": True, "pct_change_5d": 50.0}
    res = apply_risk_overrides(ctx, "STRONG_BUY")
    assert "R7" in res.hits
    assert res.level == "WATCH"


def test_disabled_config_no_override():
    res = apply_risk_overrides({"pct_change_5d": 99.0}, "BUY", {"enabled": 0})
    assert res.hits == []
    assert res.level == "BUY"


def test_no_risk_no_change():
    res = apply_risk_overrides({}, "BUY")
    assert res.hits == []
    assert res.level == "BUY"


def test_build_ctx_extracts_from_df():
    import pandas as pd
    closes = [10 + i * 0.1 for i in range(30)]  # 稳步上涨
    df = pd.DataFrame({"收盘": closes})
    ctx = build_stock_ctx("600000", "BUY", historical_df=df)
    assert ctx["pct_change_5d"] is not None
    assert ctx["rsi14"] is not None
    assert ctx["near_high_20d"] is True


def test_min_rank():
    assert min_rank("STRONG_BUY", "WATCH") == "WATCH"
    assert min_rank("BUY", "WATCH") == "WATCH"
    assert min_rank("WATCH", "BUY") == "WATCH"
