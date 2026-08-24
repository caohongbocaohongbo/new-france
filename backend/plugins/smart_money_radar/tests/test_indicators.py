from backend.plugins.smart_money_radar.indicators import (
    active_buy_ratio,
    main_fund_metrics,
    order_book_strength,
    price_returns,
    vwap_deviation,
    distance_from_high,
    price_impact,
    volume_ratio,
    large_order_amounts,
)


def test_order_book_strength_handles_empty_ask_side_as_full_bid_strength():
    quote = {
        "bid1": 10,
        "bid_vol1": 100,
        "ask1": 10.1,
        "ask_vol1": 0,
    }

    result = order_book_strength(quote)

    assert result["strength_amt"] == 100.0
    assert result["bid_amt"] == 1000.0
    assert result["ask_amt"] == 0.0


def test_order_book_strength_uses_all_five_levels():
    quote = {
        "bid1": 10, "bid_vol1": 10,
        "bid2": 9, "bid_vol2": 10,
        "bid3": 8, "bid_vol3": 10,
        "bid4": 7, "bid_vol4": 10,
        "bid5": 6, "bid_vol5": 10,
        "ask1": 10, "ask_vol1": 10,
        "ask2": 11, "ask_vol2": 10,
        "ask3": 12, "ask_vol3": 10,
        "ask4": 13, "ask_vol4": 10,
        "ask5": 14, "ask_vol5": 10,
    }

    result = order_book_strength(quote)

    assert result["strength_amt"] == 40.0


def test_active_buy_ratio_defends_zero_denominator():
    assert active_buy_ratio({"b_vol": 0, "s_vol": 0}) is None
    assert active_buy_ratio({"b_vol": 70, "s_vol": 30}) == 0.7


def test_main_fund_metrics_accepts_direct_and_fallback_fields():
    direct = main_fund_metrics({"main_net_inflow": 1200, "main_inflow_ratio": 58})
    # 回退字段名必须与 principal_capital 三个数据源保持一致：super_net / big_net
    fallback = main_fund_metrics({"super_net": 500, "big_net": 300})

    assert direct["main_net_inflow"] == 1200.0
    assert direct["main_inflow_ratio"] == 58.0
    assert fallback["main_net_inflow"] == 800.0
    assert fallback["main_inflow_ratio"] is None


def test_large_order_amounts_exposes_indicators_one_and_two_with_zero_safe_fallback():
    assert large_order_amounts({"r1_in": 1200, "r1_out": 800}) == {
        "large_buy_amount": 1200.0,
        "large_sell_amount": 800.0,
    }
    assert large_order_amounts({}) == {"large_buy_amount": None, "large_sell_amount": None}


def test_price_returns_supports_tdx_periods_and_skips_bad_bars():
    assert price_returns({
        8: [{"close": 12, "vol": 100}, {"close": 13, "vol": 100}],
        0: [{"close": 10, "vol": 100}, {"close": 11, "vol": 100}],
        1: [{"close": 8, "vol": 100}, {"close": 10, "vol": 100}],
    }) == {
        "return_1m": round((13 - 12) / 12, 4),
        "return_5m": round((11 - 10) / 10, 4),
        "return_15m": round((10 - 8) / 8, 4),
    }


def test_vwap_and_high_ignore_collection_auction_bars():
    bars = [
        {"close": 10, "high": 10.5, "low": 9.8, "vol": 100, "amount": 100000},
        {"close": 11, "high": 11.2, "low": 10.8, "vol": 0.0001, "amount": 1e-30},
    ]
    assert vwap_deviation(10, bars) == 0.0
    assert distance_from_high(10, bars) == round((11.2 - 10) / 11.2, 4)


def test_price_impact_defends_zero_inputs():
    assert price_impact(0.1, 1000, 10, 100000) == round(0.1 / (1000 / (10 * 100000)), 4)
    assert price_impact(0.1, 0, 10, 100000) is None
    assert price_impact(0.1, 1000, 0, 100000) is None


def test_volume_ratio_uses_same_time_history_and_defends_zero_average():
    current = [{"time": "09:31", "vol": 120}, {"time": "09:32", "vol": 180}]
    history = {"09:31": [100, 120], "09:32": [200, 160]}
    assert volume_ratio(current, history) == round(300 / ((100 + 120 + 200 + 160) / 2), 4)
    assert volume_ratio(current, {"09:31": [0], "09:32": [0]}) is None


def test_volume_ratio_matches_only_current_minutes_with_uneven_history():
    current = [{"time": "09:31", "vol": 120}, {"time": "09:32", "vol": 180}]
    history = {
        "09:31": [100, 120, 140],
        "09:32": [160, 200],
        "09:33": [99999],
    }
    assert volume_ratio(current, history) == round(300 / (120 + 180), 4)
