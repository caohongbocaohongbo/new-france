"""08 逐笔成交行为单测（离线）。"""
from backend.plugins.smart_money_radar.orderflow import (
    classify_transactions, continuous_attack_count, large_order_summary,
    split_large_flag, summarize_minute, tx_amount, wash_trade_flag,
)


def _tx(price, vol, side, num, time="10:00"):
    return {"price": price, "vol": vol, "buyorsell": side, "num": num, "time": time}


def test_tx_amount():
    assert tx_amount(_tx(10.0, 100, 0, 1)) == 10.0 * 100 * 100


def test_large_order_summary():
    txs = [_tx(10.0, 2000, 0, 1), _tx(10.0, 1500, 1, 2), _tx(10.0, 10, 0, 3)]
    enriched = classify_transactions(txs, large_threshold=1_000_000)
    s = large_order_summary(enriched)
    assert s["large_buy_count"] == 1  # 2000*10*100=200万 >100万
    assert s["large_sell_count"] == 1  # 1500*10*100=150万
    assert s["large_buy"] == 10.0 * 2000 * 100


def test_continuous_attack_count():
    txs = [_tx(10, 1, 0, i) for i in range(1, 6)]
    enriched = classify_transactions(txs, large_threshold=1e12)  # 全非大单
    assert continuous_attack_count(enriched, same_side=0, min_count=3) == 3


def test_split_large_flag():
    # 同分钟多笔小买单合计达大单规模 → 拆单嫌疑
    txs = [_tx(10.0, 800, 0, i) for i in range(1, 4)]  # 单笔 80万 <100万，合计 240万
    enriched = classify_transactions(txs, large_threshold=1_000_000)
    assert split_large_flag(enriched, 1_000_000) is True


def test_wash_trade_flag():
    buckets = {"10:00": {"buy_amt": 100, "sell_amt": 100, "neutral_amt": 400, "count": 3}}
    assert wash_trade_flag(buckets) is True  # 中性占比 66% 且买卖净额≈0
    buckets2 = {"10:00": {"buy_amt": 500, "sell_amt": 100, "neutral_amt": 0, "count": 2}}
    assert wash_trade_flag(buckets2) is False


def test_summarize_minute():
    buckets = {"10:00": {"buy_amt": 100, "sell_amt": 50, "neutral_amt": 0, "count": 2}}
    rows = summarize_minute(buckets)
    assert rows[0]["minute"] == "10:00"
    assert rows[0]["buy_amt"] == 100
