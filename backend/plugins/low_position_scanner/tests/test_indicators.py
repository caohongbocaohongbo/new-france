"""16 低位涨停选股器单测（修复版：AND-OR 低位 + 负向动能 + 四级降级）。"""
import datetime

import pandas as pd

from backend.plugins.low_position_scanner.indicators import (
    accel_down_factor, compute_low_score, is_below_ma, is_low_position,
    price_percentile, pullback_from_high,
)
from backend.plugins.low_position_scanner.service import (
    _coarse_filter, evaluate_candidate, resolve_zt_map,
)


def _low_not_diving():
    # 高位横盘 40 日 → 下跌 60 日 → 低位横盘（最后5日有反弹，非连续下跌）
    return [100] * 40 + list(range(100, 40, -1)) + [40] * 15 + [39, 40, 39, 40, 39]


def _pullback_not_low_percentile():
    # 回撤深但 120 日百分位 >30%（区间被历史低位拉低），现价在 MA20 下 → AND-OR 应入选
    return [20] * 70 + [20 + 4 * i for i in range(20)] + [96 - 2 * i for i in range(20)] + [58] * 10


def test_pullback_from_high():
    assert pullback_from_high([10, 20, 30, 40, 50, 100, 60], window=6) == round((60 - 100) / 100, 4)
    assert pullback_from_high([1, 2], window=60) is None


def test_price_percentile():
    assert price_percentile(list(range(1, 13)), window=12) == 1.0
    assert price_percentile([1, 2], window=120) is None


def test_is_below_ma():
    assert is_below_ma(list(range(30, 9, -1)), period=20) is True
    assert is_below_ma(list(range(1, 31)), period=20) is False


def test_is_low_position_and_or():
    # 回撤满足 + 百分位不满足 + MA20 满足 → 应入选（AND-OR 修复）
    ok, _ = is_low_position(_pullback_not_low_percentile())
    assert ok is True
    # 三维全过 → 入选
    ok2, _ = is_low_position(_low_not_diving())
    assert ok2 is True


def test_is_low_position_pullback_required():
    # 回撤不满足 → 即使百分位/MA20 满足也剔除
    closes = list(range(30, 150))  # 上升趋势，无回撤
    ok, detail = is_low_position(closes)
    assert ok is False


def test_accel_down_factor_not_hard_filter():
    assert accel_down_factor([10, 9, 8, 7, 6, 5], window=5) == 0.20
    assert accel_down_factor([5, 6, 7, 8, 9, 10], window=5) == 0.0
    assert accel_down_factor([1, 2], window=5) == 0.0


def test_compute_low_score_range_and_penalty():
    s1 = compute_low_score(-0.40, 0.20, 3, 0.0)
    s2 = compute_low_score(-0.40, 0.20, 3, 0.20)
    assert 0 <= s2 <= 100
    assert s2 < s1  # 动能惩罚降低分数
    assert compute_low_score(-0.60, 0.0, 5, 0.0) > compute_low_score(-0.25, 0.30, 1, 0.0)


def test_evaluate_candidate_zt_available_filter():
    closes = _low_not_diving()
    # zt 数据可用但无记录 → 剔除
    assert evaluate_candidate("600000", "A", 39.0, 1e8, 5e9, closes, {}, "db", True) is None
    # zt 数据不可用 → 仍入选并标注
    hit = evaluate_candidate("600000", "A", 39.0, 1e8, 5e9, closes, {}, None, False)
    assert hit is not None
    assert hit["zt_available"] is False


def test_evaluate_candidate_with_zt():
    closes = _low_not_diving()
    zt_map = {"600000": {"last_zt_date": "2026-08-01", "zt_count": 2}}
    hit = evaluate_candidate("600000", "A", 39.0, 1e8, 5e9, closes, zt_map, "db", True)
    assert hit is not None
    assert hit["zt_count_250d"] == 2
    assert hit["accel_down_penalty"] >= 0


def test_coarse_filter_default_main_board_only():
    df = pd.DataFrame([
        {"code": "600000", "name": "A", "price": 10.0, "change_pct": 1.0, "total_amount": 1e8,
         "main_inflow_ratio": 1.0, "float_mcap": 5e9},
        {"code": "000001", "name": "ST股", "price": 10.0, "change_pct": 1.0, "total_amount": 1e8,
         "main_inflow_ratio": 1.0, "float_mcap": 5e9},
        {"code": "300001", "name": "B", "price": 10.0, "change_pct": 1.0, "total_amount": 1e8,
         "main_inflow_ratio": 1.0, "float_mcap": 5e9},
        {"code": "688001", "name": "C", "price": 10.0, "change_pct": 1.0, "total_amount": 1e8,
         "main_inflow_ratio": 1.0, "float_mcap": 5e9},
        {"code": "600001", "name": "D", "price": 10.0, "change_pct": 1.0, "total_amount": 1e8,
         "main_inflow_ratio": 1.0, "float_mcap": 1e9},
    ])
    assert set(_coarse_filter(df)["code"].tolist()) == {"600000"}


def test_coarse_filter_show_gem():
    df = pd.DataFrame([
        {"code": "600000", "name": "A", "price": 10.0, "change_pct": 1.0, "total_amount": 1e8,
         "main_inflow_ratio": 1.0, "float_mcap": 5e9},
        {"code": "300001", "name": "B", "price": 10.0, "change_pct": 1.0, "total_amount": 1e8,
         "main_inflow_ratio": 1.0, "float_mcap": 5e9},
    ])
    cfg = {"show_gem": True, "show_star": False, "min_amount": 5e7, "mcap_min": 2e9, "mcap_max": 3e10}
    codes = set(_coarse_filter(df, cfg)["code"].tolist())
    assert codes == {"600000", "300001"}


def test_resolve_zt_map_four_level():
    t = datetime.date(2026, 8, 20)
    # 全部空 → unavailable
    m, src, ok = resolve_zt_map(t, {"db": lambda: {}, "tushare": lambda: {}, "akshare": lambda: {}})
    assert (m, src, ok) == ({}, None, False)
    # db 有数据 → 优先 db
    m2, src2, ok2 = resolve_zt_map(t, {"db": lambda: {"600000": {"zt_count": 1, "last_zt_date": "x"}},
                                        "tushare": lambda: {"000001": {"zt_count": 1, "last_zt_date": "y"}}})
    assert (src2, ok2) == ("db", True)
    # db/tushare 空 → akshare
    m3, src3, ok3 = resolve_zt_map(t, {"db": lambda: {}, "tushare": lambda: {},
                                       "akshare": lambda: {"600000": {"zt_count": 1, "last_zt_date": "x"}}})
    assert (src3, ok3) == ("akshare", True)
