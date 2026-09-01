"""01 情绪周期指标单测（离线）。"""
import pandas as pd

from backend.plugins.emotion_cycle.indicators import (
    break_rate, classify_regime, compute_emotion, ladder_stats, parse_zt_records, promotion_rate,
)


def _pool(rows):
    return pd.DataFrame([
        {"代码": c, "名称": n, "连板数": h, "炸板次数": b, "封板时间": 100000, "所属行业": "T", "涨跌幅": 10.0}
        for c, n, h, b in rows
    ])


def test_parse_zt_records():
    recs = parse_zt_records(_pool([("600000", "A", 3, 0), ("000001", "B", 1, 2)]))
    assert len(recs) == 2
    assert recs[0]["height"] == 3
    assert recs[1]["break_count"] == 2


def test_ladder_stats():
    recs = parse_zt_records(_pool([("600000", "A", 3, 0), ("000001", "B", 1, 0), ("600002", "C", 1, 1), ("000003", "D", 2, 0)]))
    ladder = ladder_stats(recs)
    assert ladder["first"] == 2
    assert ladder["second"] == 1
    assert ladder["third"] == 1
    assert ladder["max_height"] == 3
    assert ladder["leaders"][0]["code"] == "600000"


def test_break_rate():
    recs = parse_zt_records(_pool([("600000", "A", 1, 0), ("000001", "B", 1, 3), ("600002", "C", 1, 0)]))
    assert round(break_rate(recs), 4) == round(1 / 3, 4)


def test_promotion_rate_no_lookahead():
    today = {"600000": 2, "000001": 1}
    prev = {"600000": 1, "000002": 3}
    # 600000 从 1 板晋级 2 板；000002 昨日未涨停今日不在 → 未晋级
    assert promotion_rate(today, prev) == 0.5
    assert promotion_rate(today, {}) is None


def test_compute_emotion_empty():
    out = compute_emotion([])
    assert out["regime"] == "no_data"
    assert out["score"] is None


def test_compute_emotion_score_range():
    recs = parse_zt_records(_pool([(f"60{i:04d}", f"n{i}", 1, 0) for i in range(60)]))
    out = compute_emotion(recs, index_gain=1.0)
    assert 0 <= out["score"] <= 100
    assert out["metrics"]["zt_count"] == 60
    assert out["metrics"]["max_height"] == 1


def test_classify_regime():
    assert classify_regime(10) == "冰点"
    assert classify_regime(30) == "修复"
    assert classify_regime(50) == "发酵"
    assert classify_regime(80) == "高潮"
    assert classify_regime(60, prev_score=85) == "退潮"
    assert classify_regime(None) == "no_data"
