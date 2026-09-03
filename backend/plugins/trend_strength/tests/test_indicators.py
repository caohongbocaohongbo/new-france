"""19 趋势强度选股单测（离线）。"""
from backend.plugins.trend_strength.indicators import (
    compute_trend_score, is_ma_aligned, is_new_high, ma_values, volume_ratio,
)


def test_ma_values():
    closes = list(range(1, 70))
    ma = ma_values(closes)
    assert ma["ma5"] == sum(closes[-5:]) / 5
    assert ma["ma60"] == sum(closes[-60:]) / 60


def test_is_ma_aligned():
    # 上升趋势 → MA 多头排列
    assert is_ma_aligned(list(range(1, 70))) is True
    # 下降趋势 → 非多头
    assert is_ma_aligned(list(range(70, 0, -1))) is False


def test_is_ma_aligned_insufficient():
    assert is_ma_aligned([1, 2, 3]) is False


def test_is_new_high():
    # 创 60 日新高
    closes = [100] * 60 + [110]
    ok, prev = is_new_high(closes, 60)
    assert ok is True and prev == 100
    # 未创新高
    closes2 = [100] * 60 + [99]
    ok2, _ = is_new_high(closes2, 60)
    assert ok2 is False


def test_volume_ratio():
    assert volume_ratio([100] * 5 + [200]) == 2.0
    assert volume_ratio([200] * 5 + [100]) == 0.5


def test_compute_trend_score():
    closes = list(range(1, 70))
    vr = 2.0
    s = compute_trend_score(closes, [100] * 69, 100, 50, vr)
    assert 0 <= s <= 100
