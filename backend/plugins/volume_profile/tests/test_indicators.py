"""11 分价成交指标单测（离线）。"""
from backend.plugins.volume_profile.indicators import (
    build_price_distribution, main_cost_band, poc_price, profit_ratio, vwap,
)


def _bars():
    return [
        {"open": 10.0, "high": 10.4, "low": 9.9, "close": 10.2, "vol": 1000, "amount": 10.1 * 1000 * 100},
        {"open": 10.2, "high": 10.5, "low": 10.1, "close": 10.3, "vol": 2000, "amount": 10.25 * 2000 * 100},
    ]


def test_vwap():
    bars = _bars()
    total_vol = 3000
    total_amount = 10.1 * 1000 * 100 + 10.25 * 2000 * 100
    assert vwap(bars) == round(total_amount / (total_vol * 100), 4)


def test_vwap_empty():
    assert vwap([]) is None


def test_build_price_distribution():
    dist = build_price_distribution(_bars(), step=0.1)
    assert dist
    assert all(r["volume"] >= 0 for r in dist)
    assert dist[-1]["cumulative_ratio"] == 1.0


def test_poc_and_profit_ratio():
    dist = build_price_distribution(_bars(), step=0.1)
    poc = poc_price(dist)
    assert poc is not None
    pr = profit_ratio(dist, 10.5)
    assert pr == 1.0


def test_main_cost_band():
    dist = build_price_distribution(_bars(), step=0.1)
    band = main_cost_band(dist)
    assert band["low"] is not None and band["high"] is not None


def test_empty_distribution():
    assert build_price_distribution([]) == []
    assert poc_price([]) is None
    assert profit_ratio([], 10.0) is None
