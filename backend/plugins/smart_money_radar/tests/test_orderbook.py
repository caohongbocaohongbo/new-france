"""07 盘口委托失衡单测（离线）。"""
from backend.plugins.smart_money_radar.indicators import (
    ask_pressure_decay, bid_ask_imbalance, bid_buildup, bid_buildup_signal,
    top_concentration, withdraw_pressure_signal,
)


def _quote(bid_vols, ask_vols):
    q = {}
    for i in range(1, 6):
        q[f"bid{i}"] = 10.0 - i * 0.01
        q[f"ask{i}"] = 10.0 + i * 0.01
        q[f"bid_vol{i}"] = bid_vols[i - 1]
        q[f"ask_vol{i}"] = ask_vols[i - 1]
    return q


def test_bid_ask_imbalance_all_buy_all_sell_empty():
    assert bid_ask_imbalance(_quote([100, 0, 0, 0, 0], [0, 0, 0, 0, 0])) == 1.0
    assert bid_ask_imbalance(_quote([0, 0, 0, 0, 0], [100, 0, 0, 0, 0])) == -1.0
    assert bid_ask_imbalance(_quote([0, 0, 0, 0, 0], [0, 0, 0, 0, 0])) is None


def test_top_concentration():
    assert top_concentration(_quote([100, 0, 0, 0, 0], [0, 0, 0, 0, 0])) == 1.0
    assert top_concentration(_quote([0, 0, 0, 0, 0], [0, 0, 0, 0, 0])) is None


def test_ask_pressure_decay_and_bid_buildup():
    assert ask_pressure_decay([100, 80, 60]) < 0  # 卖一量递减 → 负斜率
    assert bid_buildup([60, 80, 100]) > 0       # 买一量递增 → 正斜率
    assert ask_pressure_decay([50]) == 0.0


def test_withdraw_pressure_signal():
    # 卖一量骤减且价格未跌 → 触发
    assert withdraw_pressure_signal([100, 50], [10.0, 10.0]) is True
    # 价格下跌 → 不触发
    assert withdraw_pressure_signal([100, 50], [10.0, 9.0]) is False
    # 降幅不足 → 不触发
    assert withdraw_pressure_signal([100, 90], [10.0, 10.0]) is False
    # 空序列 → 不触发
    assert withdraw_pressure_signal([100]) is False


def test_bid_buildup_signal():
    assert bid_buildup_signal([100, 160]) is True   # 上升 60%
    assert bid_buildup_signal([100, 120]) is False  # 上升 20%
    assert bid_buildup_signal([]) is False
