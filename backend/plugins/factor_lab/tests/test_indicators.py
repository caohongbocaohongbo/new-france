"""06 因子实验室统计单测（离线）。"""
import pandas as pd

from backend.plugins.factor_lab.indicators import (
    build_factor_stats, ic_ir, layer_returns, rank_ic,
)


def test_rank_ic_spearman():
    assert rank_ic([1, 2, 3, 4], [0.1, 0.2, 0.3, 0.4]) == 1.0
    assert rank_ic([1, 2, 3, 4], [0.4, 0.3, 0.2, 0.1]) == -1.0


def test_rank_ic_insufficient():
    assert rank_ic([1, 1, 1], [0.1, 0.2, 0.3]) is None
    assert rank_ic([1, 2], [0.1, 0.2]) is None


def test_ic_ir():
    out = ic_ir([0.2, 0.3, 0.25])
    assert out["mean"] == round(0.25, 4)
    assert out["ir"] is not None
    assert ic_ir([])["mean"] is None


def test_layer_returns():
    layers = layer_returns([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0], n_layers=5)
    assert len(layers) == 5
    assert all(l["count"] > 0 for l in layers)
    avg = [l["avg_return"] for l in layers]
    assert avg == sorted(avg)


def test_build_factor_stats():
    panel = pd.DataFrame([
        {"factor_name": "f1", "factor_value": v, "forward_ret_t1": v * 0.1}
        for v in [1, 2, 3, 4, 5, 6, 7, 8]
    ])
    stats = build_factor_stats(panel)
    assert len(stats) == 1
    assert stats[0]["factor"] == "f1"
    assert stats[0]["ic"] == 1.0
