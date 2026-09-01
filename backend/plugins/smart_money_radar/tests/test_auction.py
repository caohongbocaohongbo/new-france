"""13 集合竞价异动单测（离线）。"""
from datetime import datetime, timedelta, timezone

from backend.plugins.smart_money_radar.auction import (
    auction_amount, auction_gap, is_in_auction_window, match_vol_slope, prev_zt_auction_strength,
    withdraw_lure,
)

BEIJING_TZ = timezone(timedelta(hours=8))


def test_auction_gap_zero_last_close():
    assert auction_gap({"price": 11.0, "last_close": 10.0}) == 0.1
    assert auction_gap({"price": 11.0, "last_close": 0}) is None  # 昨收=0 防除零
    assert auction_gap({"price": 9.0, "last_close": 10.0}) == -0.1


def test_auction_amount():
    assert auction_amount({"price": 10.0, "vol": 100}) == 10.0 * 100 * 100


def test_match_vol_slope():
    assert match_vol_slope([100, 200, 300]) > 0
    assert match_vol_slope([300, 200, 100]) < 0
    assert match_vol_slope([]) == 0.0


def test_withdraw_lure():
    # 挂单骤减且开盘价下移 → 诱多撤单
    assert withdraw_lure([100, 40], [10.0, 9.5]) is True
    # 放量承接（挂单不减反增）→ 不触发
    assert withdraw_lure([100, 150], [10.0, 10.2]) is False
    # 挂单骤减但价格上行 → 不触发
    assert withdraw_lure([100, 40], [10.0, 10.5]) is False


def test_is_in_auction_window():
    assert is_in_auction_window(datetime(2026, 8, 18, 9, 18, tzinfo=BEIJING_TZ)) is True
    assert is_in_auction_window(datetime(2026, 8, 18, 9, 15, tzinfo=BEIJING_TZ)) is True
    assert is_in_auction_window(datetime(2026, 8, 18, 9, 26, tzinfo=BEIJING_TZ)) is False
    assert is_in_auction_window(datetime(2026, 8, 18, 9, 10, tzinfo=BEIJING_TZ)) is False


def test_prev_zt_auction_strength():
    class Store:
        auction_frames = {
            "600000": [{"quote": {"price": 11.0, "last_close": 10.0}}],
            "000001": [{"quote": {"price": 9.0, "last_close": 10.0}}],
        }

    store = Store()
    # 无昨日涨停代码 → 静默跳过（01 未落地）
    out = prev_zt_auction_strength(store, set(), None)
    assert out["available"] is False
    # 有昨日涨停代码 → 计算竞价强度
    out2 = prev_zt_auction_strength(store, {"600000"}, None)
    assert out2["available"] is True
    assert out2["count"] == 1
    assert out2["avg_gap"] == 0.1
