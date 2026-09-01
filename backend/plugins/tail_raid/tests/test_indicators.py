"""04 尾盘抢筹指标单测（离线）。"""
import pandas as pd

from backend.plugins.tail_raid.indicators import (
    is_tail_candidate, tail_acceleration, tail_fund_strength, tail_raid_score,
)
from backend.plugins.tail_raid.service import build_tail_rows


def test_tail_acceleration():
    assert tail_acceleration(3.0, 5.0) == 2.0
    assert tail_acceleration(5.0, 3.0) == -2.0
    assert tail_acceleration(None, 5.0) is None


def test_tail_fund_strength():
    assert tail_fund_strength(1e7, 1e8) == 0.1
    assert tail_fund_strength(1e7, 0) is None  # 防除零


def test_tail_raid_score_range():
    s = tail_raid_score(5.0, 2.0, 20.0, 1.0)
    assert 0 <= s <= 100


def test_is_tail_candidate():
    assert is_tail_candidate(5.0) is True
    assert is_tail_candidate(2.0) is False   # 涨幅不足
    assert is_tail_candidate(9.5) is False   # 接近涨停
    assert is_tail_candidate(5.0, is_limit_up=True) is False  # 已涨停剔除


def test_build_tail_rows_filters_and_sorts():
    df = pd.DataFrame([
        {"code": "600000", "name": "A", "price": 10.0, "change_pct": 5.0, "volume_ratio": 2.0,
         "main_inflow_ratio": 20.0, "total_amount": 1e8, "main_net_inflow": 1e7, "turnover": 5.0},
        {"code": "000001", "name": "B", "price": 11.0, "change_pct": 1.0, "volume_ratio": 1.0,
         "main_inflow_ratio": 0.0, "total_amount": 1e8, "main_net_inflow": 0, "turnover": 2.0},
        {"code": "600001", "name": "C", "price": 12.0, "change_pct": 6.0, "volume_ratio": 3.0,
         "main_inflow_ratio": 40.0, "total_amount": 1e8, "main_net_inflow": 2e7, "turnover": 8.0},
    ])
    rows = build_tail_rows(df)
    assert len(rows) == 2  # B 涨幅不足被剔除
    assert rows[0]["code"] == "600001"  # 高分排前
