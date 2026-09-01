"""10 涨停封单指标单测（离线）。"""
import pandas as pd

from backend.plugins.zt_seal.indicators import (
    reseal_probability, seal_amount, seal_drop_alert, seal_ratio, seal_vol,
)
from backend.plugins.zt_seal.service import build_seal_rows


def test_seal_amount_missing_returns_none():
    assert seal_amount(None, 10.0) is None
    assert seal_amount(0, 10.0) is None
    assert seal_amount(1e8, 10.0) == 1e8


def test_seal_vol_and_ratio_divzero():
    assert seal_vol(1e8, 10.0) == 1e7
    assert seal_vol(1e8, 0) is None  # 价格=0 防除零
    assert seal_ratio(1e8, 0) is None  # 流通市值=0 防除零
    assert seal_ratio(1e8, 1e10) == 0.01


def test_seal_drop_alert():
    assert seal_drop_alert([100, 40]) is True   # 降 60%
    assert seal_drop_alert([100, 90]) is False  # 降 10%
    assert seal_drop_alert([100]) is False


def test_reseal_probability():
    assert reseal_probability([True, True, False]) == round(2 / 3, 4)
    assert reseal_probability([]) == 0.0


def test_build_seal_rows():
    df = pd.DataFrame([
        {"代码": "600000", "名称": "A", "最新价": 10.0, "流通市值": 1e10, "封板资金": 2e8, "炸板次数": 0, "封板时间": 100000, "最后封板时间": 100000},
        {"代码": "000001", "名称": "B", "最新价": 12.0, "流通市值": 2e10, "封板资金": None, "炸板次数": 2, "封板时间": 101000, "最后封板时间": 103000},
    ])
    rows = build_seal_rows(df)
    assert rows[0]["code"] == "600000"  # 有封板资金排前
    assert rows[0]["seal_amount"] == 2e8
    assert rows[1]["seal_amount"] is None  # 源字段缺失不编造
