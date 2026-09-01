"""15 四维共振信号单测（修复版：D2/D3 None 重分配 + 持续性 + 阈值只读）。"""
import pandas as pd

from backend.plugins.resonance.indicators import (
    compute_resonance, d1_score, d2_score, d3_score, d4_score,
)
from backend.plugins.resonance.service import (
    _suggested_adjustment, coarse_filter, compute_d1, evaluate_resonance,
)


class _Row:
    def __init__(self, score):
        self.score = score


class _Query:
    def join(self, *a):
        return self

    def filter(self, *a):
        return self

    def order_by(self, *a):
        return self

    def first(self):
        return _Row(60.0)


class _Session:
    def query(self, *a):
        return _Query()


def test_d1_score_continuity_bonus():
    assert d1_score(0.03, "吸筹", 0) < d1_score(0.03, "吸筹", 10)
    assert d1_score(0.03, "拉升", 0) > d1_score(0.03, "吸筹", 0) > d1_score(0.03, "观望", 0) > d1_score(0.03, "出货", 0)
    assert d1_score(0.03, "吸筹", 100) - d1_score(0.03, "吸筹", 10) == 0  # 持续性加成封顶 10


def test_d2_score_insufficient_returns_none():
    assert d2_score([1, 2], None) is None
    up = d2_score(list(range(1, 31)), list(range(1, 31)))
    down = d2_score(list(range(30, 0, -1)), list(range(30, 0, -1)))
    assert up > down


def test_d3_score_two_paths_and_degraded():
    board_heat = {"items": [{"name": "半导体", "score": 3.0, "codes": ["600000"]}]}
    assert d3_score("600000", board_heat, None) == (3.0, False, "snapshot")
    assert d3_score("999999", board_heat, None) == (None, True, "unavailable")
    # db 路径
    s, deg, src = d3_score("600000", None, _Session())
    assert (deg, src) == (False, "db") and s == 60.0


def test_d4_score_volume_ratio():
    up = d4_score([100, 100, 100, 100, 100, 100, 200])
    down = d4_score([200, 200, 200, 200, 200, 200, 100])
    assert up[1] > 1.0 and down[1] < 1.0
    assert up[0] > down[0]
    assert d4_score([100]) == (50.0, 1.0)  # 数据不足


def test_compute_resonance_normal_red():
    r = compute_resonance(80.0, "吸筹", 80.0, (70.0, False, "snapshot"), (80.0, 1.5))
    assert r["resonance_score"] == round(80 * 0.35 + 80 * 0.25 + 70 * 0.20 + 80 * 0.20, 2)
    assert r["signal"] == "RED"
    assert r["red_threshold"] == 75.0


def test_compute_resonance_d3_degraded_weight_redistribution():
    r = compute_resonance(80.0, "吸筹", 80.0, (None, True, "unavailable"), (80.0, 1.5))
    assert r["resonance_score"] == round(80 * 0.45 + 80 * 0.30 + 80 * 0.25, 2)
    assert r["red_threshold"] == 78.0
    assert r["d3_degraded"] is True


def test_compute_resonance_signal_state_gate():
    # 高分但 D1 出货 → 不 RED
    r = compute_resonance(80.0, "出货", 80.0, (70.0, False, "snapshot"), (80.0, 1.5))
    assert r["signal"] != "RED"


def test_compute_d1_state():
    row = {"total_amount": 1e8, "super_net": 3e6, "big_net": 2e6, "change_pct": 0.5}
    assert compute_d1(row)["state"] == "吸筹"


def test_coarse_filter_default_main_board():
    df = pd.DataFrame([
        {"code": "600000", "name": "A", "price": 10.0, "change_pct": 0.5, "total_amount": 1e8,
         "main_inflow_ratio": 5.0, "super_net": 3e6, "big_net": 2e6},
        {"code": "300001", "name": "B", "price": 10.0, "change_pct": 0.5, "total_amount": 1e8,
         "main_inflow_ratio": 5.0, "super_net": 3e6, "big_net": 2e6},
    ])
    out = coarse_filter(df)
    assert [r[0]["code"] for r in out] == ["600000"]


def test_suggested_adjustment_read_only():
    assert _suggested_adjustment(150) == "+3"
    assert _suggested_adjustment(2) == "-3"
    assert _suggested_adjustment(30) is None


def test_evaluate_resonance_full():
    d1 = {"smart_ratio": 0.05, "state": "吸筹"}
    board_heat = {"items": [{"name": "半导体", "score": 70.0, "codes": ["600000"]}]}
    closes = list(range(1, 31)); highs = list(range(1, 31)); vols = [100] * 29 + [200]
    hit = evaluate_resonance("600000", "A", 10.0, d1, 3, closes, highs, vols, board_heat, None)
    assert hit is not None
    assert hit["d1_continuity_rounds"] == 3
    assert hit["d3_degraded"] is False
    assert hit["d3_source"] == "snapshot"
