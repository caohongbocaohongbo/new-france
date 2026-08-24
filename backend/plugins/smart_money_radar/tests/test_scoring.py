from backend.plugins.smart_money_radar.scoring import launch_score, smart_money_score


def test_smart_money_score_normalizes_bounds_and_returns_zero_for_missing():
    assert smart_money_score({}) == 0.0
    assert smart_money_score({"active_buy_ratio": 1, "price_impact": 0, "volume_ratio": 3,
                              "decay_score": 1, "strength_smooth": 100,
                              "vwap_deviation": 0.05, "return_1m": 0.1,
                              "fund_persistence": 5, "main_inflow_ratio": 100}) == 95.0


def test_smart_money_high_launch_low_is_supported():
    metrics = {
        "active_buy_ratio": 0.9, "fund_persistence": 5, "price_impact": 0.1,
        "volume_ratio": 1.3, "decay_score": 0.8, "strength_smooth": 90,
        "vwap_deviation": 0.01, "return_1m": 0.01, "main_inflow_ratio": 80,
        "buy_trend": 0.1, "distance_from_high": 0.03, "breakout": 0,
    }
    assert smart_money_score(metrics) > 70
    assert launch_score(metrics) < 70


def test_scoring_weights_can_be_overridden():
    metrics = {"active_buy_ratio": 1}
    assert smart_money_score(metrics, {"active_buy_ratio": 100}) == 100.0


def test_negative_price_impact_is_clipped_before_inverse_normalization():
    zero_score = smart_money_score({"price_impact": 0})
    negative_score = smart_money_score({"price_impact": -3})
    mid_score = smart_money_score({"price_impact": 5})
    high_score = smart_money_score({"price_impact": 10})

    assert negative_score == zero_score
    assert mid_score == 10.0
    assert high_score == 0.0
