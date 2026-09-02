"""17 盘中实时化 + 推送单测（离线）。"""
from datetime import datetime, timedelta, timezone

from backend.plugins.resonance.indicators import (
    _intraday_progress, d2_score, d2_score_intraday, d4_score_intraday,
)
from backend.plugins.resonance.notifier import build_email_payload, notify_new_signals
from backend.plugins.resonance.service import evaluate_resonance

BEIJING_TZ = timezone(timedelta(hours=8))


def test_d2_score_intraday_equals_append():
    closes = list(range(1, 31))
    highs = list(range(1, 31))
    rt = 33.0
    assert d2_score_intraday(closes, highs, rt) == d2_score(closes + [rt], highs)


def test_d2_score_intraday_none_realtime():
    assert d2_score_intraday([1, 2, 3], [1, 2, 3], None) is None
    assert d2_score_intraday([1, 2, 3], [1, 2, 3], 0) is None


def test_d4_score_intraday_volume_ratio():
    # 日内量=日均量一半, 进度0.5 → 量比≈1.0
    s, vr = d4_score_intraday([100] * 10, 50, 100, time_progress=0.5)
    assert abs(vr - 1.0) < 1e-6
    # 放量：日内量翻倍 → 量比>1, 得分更高
    s2, vr2 = d4_score_intraday([100] * 10, 200, 100, time_progress=0.5)
    assert vr2 > 1.0 and s2 > s


def test_d4_score_intraday_divzero():
    assert d4_score_intraday([100] * 5, 50, 0) == (50.0, 1.0)  # prev_avg_vol=0 防除零


def test_d4_score_intraday_opening_floor():
    # 开盘初期 time_progress≈0 → 下限 0.15，避免量比虚高（15/(100*0.15)=1.0，而非 15/(100*0.05)=3.0）
    s, vr = d4_score_intraday([100] * 10, 15, 100, time_progress=0.0)
    assert abs(vr - 1.0) < 1e-6
    assert vr < 3.0


def test_intraday_progress():
    assert _intraday_progress(datetime(2026, 8, 18, 9, 0, tzinfo=BEIJING_TZ)) == 0.0
    assert _intraday_progress(datetime(2026, 8, 18, 15, 0, tzinfo=BEIJING_TZ)) == 1.0
    p = _intraday_progress(datetime(2026, 8, 18, 10, 30, tzinfo=BEIJING_TZ))
    assert 0.0 < p < 1.0


def test_evaluate_resonance_intraday():
    d1 = {"smart_ratio": 0.05, "state": "吸筹"}
    closes = list(range(1, 31)); highs = list(range(1, 31)); vols = [100] * 30
    hit = evaluate_resonance("600000", "A", 31.0, d1, 3, closes, highs, vols, {}, None,
                             is_intraday=True, realtime_price=31.0, intraday_vol=200,
                             prev_avg_vol=100, time_progress=0.5)
    assert hit is not None
    assert hit["d2_realtime"] == 1


def test_evaluate_resonance_daily_fallback():
    d1 = {"smart_ratio": 0.05, "state": "吸筹"}
    closes = list(range(1, 31)); highs = list(range(1, 31)); vols = [100] * 30
    # 盘中但无实时价 → 降级日线 d2_realtime=0
    hit = evaluate_resonance("600000", "A", 31.0, d1, 3, closes, highs, vols, {}, None,
                             is_intraday=True, realtime_price=None, intraday_vol=None,
                             prev_avg_vol=None, time_progress=0.5)
    assert hit is not None and hit["d2_realtime"] == 0


def test_build_email_payload():
    now = datetime(2026, 8, 18, 10, 30, tzinfo=BEIJING_TZ)
    red = [{"code": "600000", "name": "A", "resonance_score": 80, "d1_state": "吸筹",
            "d2_score": 70, "d3_score": 60, "d4_score": 65}]
    subject, text, html = build_email_payload(red, [], now)
    assert "红灯1" in subject
    assert "600000" in text and "600000" in html


def test_notify_new_signals_empty():
    now = datetime(2026, 8, 18, 10, 30, tzinfo=BEIJING_TZ)
    assert notify_new_signals([], [], now) == (False, None)
