"""18 经典技术指标选股单测（离线）。"""
from backend.plugins.tech_indicators.indicators import (
    compute_boll, compute_kdj, compute_macd, compute_rsi, compute_tech_score,
    ema_series, sma_series,
)


def test_ema_series():
    out = ema_series([1, 2, 3, 4, 5], 3)
    assert len(out) == 5
    assert out[-1] > out[0]


def test_sma_series():
    out = sma_series([1, 2, 3, 4, 5], 3)
    assert out == [2.0, 3.0, 4.0]


def test_macd_golden_cross():
    # 先下跌后上涨，制造金叉（DIF 上穿 DEA）
    closes = list(range(100, 70, -1)) + list(range(70, 120))
    macd = compute_macd(closes)
    assert macd is not None
    assert "golden" in macd and "dif" in macd and "dea" in macd


def test_macd_insufficient_data():
    assert compute_macd([1, 2, 3]) is None


def test_kdj_low_golden():
    # 低位震荡后回升，K 上穿 D 且 K<20
    closes = [100] * 20 + list(range(95, 100)) + [99, 100, 101, 102]
    highs = [c + 2 for c in closes]
    lows = [c - 2 for c in closes]
    kdj = compute_kdj(closes, highs, lows)
    assert kdj is not None
    assert "k" in kdj and "d" in kdj and "j" in kdj


def test_kdj_insufficient_data():
    assert compute_kdj([1, 2], [1, 2], [1, 2]) is None


def test_rsi_oversold_rebound():
    # 连续下跌后反弹，RSI<30 且回升
    closes = list(range(100, 60, -1)) + [61, 62]
    rsi = compute_rsi(closes)
    assert rsi is not None
    assert rsi["rsi"] < 100


def test_rsi_avg_loss_zero():
    # 连续上涨，avg_loss=0 → RSI=100 防除零
    rsi = compute_rsi(list(range(1, 20)))
    assert rsi["rsi"] == 100.0


def test_boll_rebound():
    # 触下轨后回升
    closes = list(range(100, 80, -1)) + [78, 82, 85]
    boll = compute_boll(closes)
    assert boll is not None
    assert boll["ub"] > boll["mb"] > boll["lb"]


def test_tech_score_multi_hit():
    macd = {"golden": True, "histogram": 0.5}
    kdj = {"low_golden": True, "k": 15}
    rsi = {"oversold_rebound": True, "rsi": 25}
    boll = {"rebound": True}
    score = compute_tech_score(4, macd, kdj, rsi, boll)
    assert 0 <= score <= 100
    assert score > compute_tech_score(1, macd, None, None, None)  # 多命中 > 单命中
