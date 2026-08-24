"""Smart Money 与启动评分。"""

import math


SMART_WEIGHTS = {
    "active_buy_ratio": 20,
    "fund_persistence": 15,
    "price_impact": 20,
    "volume_ratio": 10,
    "decay_score": 10,
    "strength_smooth": 10,
    "vwap_deviation": 5,
    "return_1m": 5,
    "sector_fund": 5,
}


def _number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _norm(value, low, high, inverse=False):
    value = _number(value)
    if value is None or high <= low:
        return 0.0
    score = min(1.0, max(0.0, (value - low) / (high - low)))
    return 1.0 - score if inverse else score


def smart_money_score(metrics: dict, weights: dict = None) -> float:
    weights = weights or SMART_WEIGHTS
    raw_price_impact = _number(metrics.get("price_impact"))
    clipped_price_impact = max(0.0, raw_price_impact) if raw_price_impact is not None else None
    factors = {
        "active_buy_ratio": _norm(metrics.get("active_buy_ratio"), 0.5, 1),
        "fund_persistence": _norm(metrics.get("fund_persistence"), 0, 5),
        "price_impact": _norm(clipped_price_impact, 0, 10, inverse=True),
        "volume_ratio": _norm(metrics.get("volume_ratio"), 1, 2),
        "decay_score": _norm(metrics.get("decay_score"), 0, 1),
        "strength_smooth": _norm(metrics.get("strength_smooth"), 50, 100),
        "vwap_deviation": _norm(metrics.get("vwap_deviation"), 0, 0.02),
        "return_1m": _norm(metrics.get("return_1m"), 0, 0.03),
        "sector_fund": 0.0,
    }
    return round(min(100.0, sum(factors.get(key, 0) * float(weight) for key, weight in weights.items())), 2)


def launch_score(metrics: dict, weights: dict = None) -> float:
    weights = weights or {"decay": 25, "buy_trend": 20, "near_high": 20, "volume": 20, "vwap": 15}
    factors = {
        "decay": _norm(metrics.get("decay_score"), 0, 1),
        "buy_trend": _norm(metrics.get("buy_trend"), 0, 1),
        "near_high": _norm(metrics.get("distance_from_high"), 0, 0.03, inverse=True),
        "volume": _norm(metrics.get("volume_ratio"), 1, 2),
        "vwap": _norm(metrics.get("vwap_deviation"), 0, 0.02),
    }
    return round(min(100.0, sum(factors.get(key, 0) * float(weight) for key, weight in weights.items())), 2)
