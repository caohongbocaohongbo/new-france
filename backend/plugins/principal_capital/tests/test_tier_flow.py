"""09 大单分层资金流状态机单测（离线 mock）。"""
import pandas as pd
import pytest

from backend.plugins.principal_capital.tier_flow import build_tier_rows, classify_state


def _df(rows):
    return pd.DataFrame(rows)


def test_classify_state_boundaries():
    # 拉升：大单净买 + 上涨
    assert classify_state(1e6, 3.0) == "拉升"
    # 吸筹：大单净买 + 横盘/微跌
    assert classify_state(1e6, 0.2) == "吸筹"
    assert classify_state(1e6, -0.5) == "吸筹"
    # 出货：大单净卖（无论涨跌）
    assert classify_state(-1e6, 1.5) == "出货"
    assert classify_state(-1e6, -3.0) == "出货"
    # 观望：净额为零/缺失
    assert classify_state(0, 0.5) == "观望"
    assert classify_state(None, 1.0) == "观望"
    assert classify_state(1e6, None) == "观望"


def test_build_tier_rows_ratio_and_sort():
    rows = _df([
        {"code": "600000", "name": "浦发", "price": 10.0, "change_pct": 2.0,
         "total_amount": 1e8, "super_net": 2e7, "big_net": 1e7, "mid_net": -1e7, "small_net": 0},
        {"code": "000001", "name": "平安", "price": 12.0, "change_pct": -1.0,
         "total_amount": 1e8, "super_net": -3e7, "big_net": -1e7, "mid_net": 0, "small_net": 0},
    ])
    out = build_tier_rows(rows)
    assert out[0]["code"] == "600000"  # smart_ratio=30 排前
    assert out[0]["smart_ratio"] == 30.0
    assert out[0]["state"] == "拉升"
    assert out[1]["smart_ratio"] == -40.0
    assert out[1]["state"] == "出货"


def test_build_tier_rows_zero_total_amount_no_divzero():
    rows = _df([
        {"code": "600000", "name": "浦发", "price": 0.0, "change_pct": 0.0,
         "total_amount": 0, "super_net": 0, "big_net": 0, "mid_net": 0, "small_net": 0},
    ])
    out = build_tier_rows(rows)
    assert out[0]["smart_ratio"] is None  # total_amount=0 防除零
    assert out[0]["super_ratio"] is None
    assert out[0]["state"] == "观望"
