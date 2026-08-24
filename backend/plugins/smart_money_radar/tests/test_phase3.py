from datetime import datetime, timezone, timedelta

from backend.plugins.smart_money_radar.indicators import sell_pressure_decay
from backend.plugins.smart_money_radar.service import transition_stage


TZ = timezone(timedelta(hours=8))


def test_sell_pressure_decay_uses_quote_and_closed_minute_paths():
    now = datetime(2026, 8, 18, 10, 30, tzinfo=TZ)
    quotes = [
        {"ts": "2026-08-18T10:29:10+08:00", "quote": {"ask_vol1": 100, "ask_vol2": 100}},
        {"ts": "2026-08-18T10:29:50+08:00", "quote": {"ask_vol1": 50, "ask_vol2": 50}},
    ]
    buckets = {"10:28": {"sell_amt": 300}, "10:29": {"sell_amt": 100}, "10:30": {"sell_amt": 999}}
    result = sell_pressure_decay(quotes, buckets, now, window_s=90, decay_minutes=2)
    assert result["decay_ask"] > 0
    assert result["decay_sell"] > 0
    assert result["decay_score"] > 0


def test_stage_machine_promotes_once_and_detects_failure():
    base = {"active_buy_ratio": 0.7, "main_net_inflow": 100, "change_pct": 0.01,
            "volume_ratio": 1.5, "vwap_deviation": 0.01, "decay_score": 0.6,
            "smart_money_score": 85, "launch_score": 85, "distance_from_high": 0.005,
            "breakout": 1, "buy_trend": 0.8}
    state = {"stage": "观察池", "qualifying_rounds": 0, "qualifying_minutes": 0}
    for _ in range(3):
        state = transition_stage(state, base)
    assert state["stage"] in {"潜伏", "吸筹确认"}
    state = {"stage": "启动前夕", "qualifying_rounds": 5, "qualifying_minutes": 5}
    failed = transition_stage(state, {**base, "main_net_inflow": -1, "vwap_deviation": -0.02})
    assert failed["stage"] == "启动失败"
    failed = transition_stage(state, {**base, "sell_surge": 2.5})
    assert failed["stage"] == "启动失败"
