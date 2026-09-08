"""20 形态突破选股单测（离线）。"""
from backend.plugins.pattern_scanner.indicators import (
    compute_pattern_score, detect_gap_hold, is_narrow_platform, is_platform_breakout,
)


def test_is_narrow_platform_narrow():
    # 窄幅平台（低波动）→ 命中
    highs = [100 + 0.5] * 60
    lows = [100 - 0.5] * 60
    ok, ph = is_narrow_platform(highs, lows, platform_days=20)
    assert ok is True and ph is not None


def test_is_narrow_platform_wide():
    # 宽幅平台（趋势上行，20日累计跨度大但日均波动小）→ 不命中
    # platform_range/20 ≈ 0.0067 > avg_daily_range × 0.6 ≈ 0.006
    highs = list(range(100, 160))
    lows = list(range(99, 159))
    ok, _ = is_narrow_platform(highs, lows, platform_days=20)
    assert ok is False


def test_is_narrow_platform_insufficient():
    ok, _ = is_narrow_platform([100] * 30, [99] * 30)
    assert ok is False


def test_is_platform_breakout():
    # 放量突破平台上沿 → 命中
    closes = [100] * 5 + [105]
    vols = [100] * 5 + [300]  # 量比 3.0
    ok, vr = is_platform_breakout(closes, vols, platform_high=102)
    assert ok is True
    # 未放量 → 不命中
    ok2, _ = is_platform_breakout([100] * 5 + [105], [100] * 6, platform_high=102)
    assert ok2 is False


def test_detect_gap_hold_common():
    # 普通缺口（gap_pct≈3%>2%，不回补；量比=1.0 未放量，开盘未破平台上沿）
    # 索引对齐：gap_idx=-4=index2(3日前)，prev_high=highs[-5]=index1(4日前最高)
    opens = [100, 100, 104, 103, 103, 103]  # index2=3日前开盘 104（缺口≈3%）
    highs = [100, 101, 105, 104, 104, 104]  # index1=4日前最高 101
    lows = [99, 99, 103, 102, 102, 102]     # index3-5 最低 102 > 101（不回补）
    vols = [100] * 6                        # 量比 1.0（未放量）
    g = detect_gap_hold(opens, highs, lows, vols, platform_high=105)
    assert g is not None
    assert g["gap_type"] == "common"  # 未放量+未破平台 → 普通缺口
    assert g["hold"] is True


def test_detect_gap_hold_breakaway():
    # 突破缺口（gap_pct≈5%>2% + 量比3.0>1.5 + 开盘106>平台上沿104）
    opens = [100, 100, 106, 105, 105, 105]  # index2=3日前开盘 106（缺口≈5%）
    highs = [100, 101, 107, 106, 106, 106]  # index1=4日前最高 101
    lows = [99, 99, 105, 104, 104, 104]     # index3-5 最低 104 > 101（不回补）
    vols = [100] * 5 + [300]                # 量比 3.0
    g = detect_gap_hold(opens, highs, lows, vols, platform_high=104)
    assert g is not None
    assert g["gap_type"] == "breakaway"  # 缺口>2% + 量比>1.5 + 破平台
    assert g["hold"] is True


def test_detect_gap_hold_no_gap():
    # 无缺口（开盘未跳空）
    opens = [100, 100, 100, 100, 100, 100]
    highs = [101, 101, 101, 101, 100, 100]
    lows = [99, 99, 99, 99, 100, 100]
    vols = [100] * 6
    g = detect_gap_hold(opens, highs, lows, vols, platform_high=104)
    assert g is None


def test_compute_pattern_score():
    s1 = compute_pattern_score(True, False, False, 0.05, 0)
    s2 = compute_pattern_score(True, True, True, 0.05, 0.03)
    assert 0 <= s1 <= 100 and 0 <= s2 <= 100
    assert s2 > s1  # 双命中 > 单命中
