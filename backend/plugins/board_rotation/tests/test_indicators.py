"""05 板块轮动指标单测（离线）。"""
from backend.plugins.board_rotation.indicators import (
    board_score_from_z, classify_stage, mainline_confirm, zscore,
)
from backend.plugins.board_rotation.service import build_board_scores, count_zt_by_industry


def test_zscore():
    assert zscore([1, 2, 3]) == [-1.2247, 0.0, 1.2247]
    assert zscore([5, 5, 5]) == [0.0, 0.0, 0.0]  # std=0
    assert zscore([]) == []


def test_board_score_monotonic():
    # 高 z 高涨停得分更高
    s1 = board_score_from_z(1.0, 1.0, 5, 3, 0.5)
    s2 = board_score_from_z(-1.0, -1.0, 0, 0, 0.0)
    assert s1 > s2


def test_classify_stage():
    assert classify_stage(3.0) == "高潮"
    assert classify_stage(1.0) == "发酵"
    assert classify_stage(0.0) == "启动"
    assert classify_stage(1.0, prev_score=3.0, zt_count=2, prev_zt_count=5) == "退潮"
    assert classify_stage(1.0, zt_count=2, prev_zt_count=5) == "分歧"


def test_mainline_confirm():
    assert mainline_confirm(5, 4) is True   # 涨停家数不衰减
    assert mainline_confirm(3, 5) is False  # 衰减
    assert mainline_confirm(5, 4, in_top_yesterday=False) is False


def test_build_board_scores_sorts():
    boards = [
        {"name": "A", "change_pct": 3.0, "net_inflow": 1e8, "zt_count": 8, "max_height": 4, "zt_ratio": 0.3},
        {"name": "B", "change_pct": -1.0, "net_inflow": -1e8, "zt_count": 0, "max_height": 0, "zt_ratio": 0.0},
    ]
    out = build_board_scores(boards)
    assert out[0]["name"] == "A"


def test_count_zt_by_industry():
    import pandas as pd
    df = pd.DataFrame([
        {"所属行业": "半导体", "连板数": 3},
        {"所属行业": "半导体", "连板数": 1},
        {"所属行业": "医药", "连板数": 1},
    ])
    agg = count_zt_by_industry(df)
    assert agg["半导体"]["zt_count"] == 2
    assert agg["半导体"]["max_height"] == 3
