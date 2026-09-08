"""18 经典技术指标选股单测（离线）。"""
from backend.plugins.tech_indicators.indicators import (
    boll_series_full, compute_boll, compute_kdj, compute_macd, compute_rsi, compute_tech_score,
    ema_series, kdj_series_full, macd_series_full, ma_series_full, rsi_series_full, sma_series,
)


def test_ema_series():
    out = ema_series([1, 2, 3, 4, 5], 3)
    assert len(out) == 3  # len-period+1 = 5-3+1（SMA 种子口径）
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
    # 触下轨后回升（需 25+ 元素：period=20 + 5日均线窗口）
    closes = list(range(100, 80, -1)) + [78, 82, 85, 86, 87, 88, 89, 90]
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


def _sample_series(n=80):
    closes = [10 + i * 0.1 + (i % 5) * 0.05 for i in range(n)]
    highs = [c + 0.2 for c in closes]
    lows = [c - 0.2 for c in closes]
    return closes, highs, lows


def test_ma_series_full_lengths_and_warmup():
    closes, _, _ = _sample_series()
    ma = ma_series_full(closes)
    assert set(ma) == {"ma5", "ma10", "ma20", "ma60"}
    for k, v in ma.items():
        assert len(v) == len(closes)
    assert ma["ma5"][4] is not None and ma["ma5"][3] is None
    assert ma["ma60"][59] is not None and ma["ma60"][58] is None


def test_macd_series_full_warmup():
    closes, _, _ = _sample_series()
    macd = macd_series_full(closes)
    assert macd is not None
    assert set(macd) == {"dif", "dea", "hist"}
    for k in ("dif", "dea", "hist"):
        assert len(macd[k]) == len(closes)
    # dif 暖机 slow-1=25，dea/hist 暖机 slow+signal-2=33
    assert macd["dif"][24] is None and macd["dif"][25] is not None
    assert macd["dea"][32] is None and macd["dea"][33] is not None


def test_kdj_series_full():
    closes, highs, lows = _sample_series()
    kdj = kdj_series_full(closes, highs, lows)
    assert kdj is not None
    assert set(kdj) == {"k", "d", "j"}
    for k in ("k", "d", "j"):
        assert len(kdj[k]) == len(closes)
        assert kdj[k][-1] is not None


def test_rsi_series_full_warmup():
    closes, _, _ = _sample_series()
    rsi = rsi_series_full(closes)
    assert rsi is not None
    assert len(rsi) == len(closes)
    assert rsi[13] is None and rsi[14] is not None
    assert 0 <= rsi[-1] <= 100


def test_boll_series_full():
    closes, _, _ = _sample_series()
    boll = boll_series_full(closes)
    assert boll is not None
    assert set(boll) == {"mb", "ub", "lb"}
    for k in ("mb", "ub", "lb"):
        assert len(boll[k]) == len(closes)
        assert boll[k][19] is not None and boll[k][18] is None
    assert boll["ub"][-1] > boll["mb"][-1] > boll["lb"][-1]
