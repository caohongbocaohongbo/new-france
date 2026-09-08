"""21 筹码集中度与获利盘选股单测（离线）。"""
from backend.plugins.chip_scanner.indicators import (
    compute_chip_score, concentration_ratio, is_chip_hit, is_tight_control,
    ma20_slope, profit_ratio,
)


def _dist():
    return [
        {"price_level": 9.0, "volume": 100},
        {"price_level": 9.5, "volume": 200},
        {"price_level": 10.0, "volume": 500},
        {"price_level": 10.5, "volume": 150},
        {"price_level": 11.0, "volume": 50},
    ]


def test_concentration_ratio():
    cr, p05, p50, p95 = concentration_ratio(_dist())
    assert cr is not None and p05 is not None and p50 is not None and p95 is not None
    assert cr >= 0
    # 90% 区间宽度 / 中心价
    assert cr == round((p95 - p05) / p50, 4)


def test_concentration_ratio_empty():
    cr, _, _, _ = concentration_ratio([])
    assert cr is None


def test_profit_ratio():
    pr = profit_ratio(_dist(), 10.0)
    # 现价 10.0 下方成本量 = 100+200+500 = 800 / 总 1000 = 0.8
    assert pr == 0.8
    assert profit_ratio(_dist(), 11.0) == 1.0
    assert profit_ratio(_dist(), 8.0) == 0.0


def test_ma20_slope():
    # 上升趋势 → MA20 上行（斜率>0）
    slope = ma20_slope(list(range(1, 30)))
    assert slope is not None and slope > 0
    # 下降趋势 → MA20 下行
    slope2 = ma20_slope(list(range(30, 0, -1)))
    assert slope2 is not None and slope2 < 0


def test_is_chip_hit():
    # 集中度高 + 获利盘>70% + 趋势向上 → 命中
    assert is_chip_hit(0.12, 0.75, True) is True
    # 趋势向下 → 不命中（防超涨）
    assert is_chip_hit(0.12, 0.75, False) is False
    # 集中度不足 → 不命中
    assert is_chip_hit(0.20, 0.75, True) is False
    # 获利盘不足 → 不命中
    assert is_chip_hit(0.12, 0.60, True) is False


def test_is_tight_control():
    assert is_tight_control(0.08, 0.90) is True
    assert is_tight_control(0.12, 0.90) is False


def test_compute_chip_score():
    s = compute_chip_score(0.10, 0.80, 0.03)
    assert 0 <= s <= 100
    s2 = compute_chip_score(0.10, 0.80, 0.0)
    assert s > s2  # 趋势强 > 趋势弱
