"""03 T+1 溢价校准单测（离线，无前视）。"""
import pandas as pd

from backend.plugins.overnight_arbitrage.calibration import (
    bucketize, build_samples_from_history, calibrate, find_index_by_date, forward_returns,
)


def _hist():
    return pd.DataFrame([
        {"日期": "2026-08-17", "开盘": 10.0, "收盘": 10.0, "最高": 10.5, "最低": 9.8},
        {"日期": "2026-08-18", "开盘": 10.2, "收盘": 10.5, "最高": 10.8, "最低": 10.1},
        {"日期": "2026-08-19", "开盘": 10.6, "收盘": 10.3, "最高": 10.9, "最低": 10.2},
    ])


def test_bucketize():
    assert bucketize(55) == "50-60"
    assert bucketize(0) == "0-10"
    assert bucketize(None) == "unknown"


def test_find_index_by_date():
    assert find_index_by_date(_hist(), "2026-08-18") == 1
    assert find_index_by_date(_hist(), "2099-01-01") is None


def test_forward_returns_no_lookahead():
    # 触发日 index=1（2026-08-18），只用 T+1（08-19）价格
    r = forward_returns(_hist(), 1)
    assert r["label_t1_open"] == round((10.6 - 10.5) / 10.5, 4)
    assert r["label_t1_close"] == round((10.3 - 10.5) / 10.5, 4)
    # 触发日=最后一行 → 无未来数据 → None
    r2 = forward_returns(_hist(), 2)
    assert r2["label_t1_close"] is None


def test_build_samples_from_history():
    history = {"records": [
        {"code": "600000", "name": "A", "recommendations": [
            {"date": "2026-08-18", "decision_score": 55.0, "action": "BUY", "price": 10.0},
            {"date": "2026-08-19", "decision_score": 42.0, "action": "WATCH", "price": 10.5},
        ]},
    ]}
    samples = build_samples_from_history(history)
    assert len(samples) == 2
    assert samples[0]["score_bucket"] == "50-60"


def test_calibrate_insufficient():
    out = calibrate([])
    assert out["insufficient"] is True
    assert out["sample_count"] == 0


def test_calibrate_buckets():
    samples = [
        {"score_bucket": "50-60", "label_t1_close": 0.02},
        {"score_bucket": "50-60", "label_t1_close": -0.01},
        {"score_bucket": "60-70", "label_t1_close": 0.05},
    ]
    out = calibrate(samples)
    assert out["sample_count"] == 3
    assert out["buckets"][0]["bucket"] == "50-60"
    assert out["buckets"][0]["win_rate"] == 0.5
