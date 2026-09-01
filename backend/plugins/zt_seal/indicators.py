"""10 涨停封单指标纯函数。"""
import math


def _float(value, default=None):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def seal_amount(fund, price):
    """封单额 = 封板资金（东财源字段，缺失返回 None 不编造）。"""
    f = _float(fund)
    return round(f, 2) if f is not None and f > 0 else None


def seal_vol(fund, price):
    """封单量 = 封板资金 / 涨停价。price<=0 防除零。"""
    f = _float(fund); p = _float(price)
    if f is None or p is None or p <= 0:
        return None
    return round(f / p, 2)


def seal_ratio(fund, float_mcap):
    """封成比 = 封单额 / 流通市值。流通市值=0 防除零。"""
    f = _float(fund); m = _float(float_mcap)
    if f is None or m is None or m <= 0:
        return None
    return round(f / m, 4)


def seal_drop_alert(series: list, drop_pct: float = 0.5) -> bool:
    """封单量 10min 下降 > 50% → 预警。series 为封单量时间序列。"""
    vals = [_float(v) for v in series or []]
    vals = [v for v in vals if v is not None]
    if len(vals) < 2 or vals[0] <= 0:
        return False
    return (vals[0] - vals[-1]) / vals[0] >= drop_pct


def reseal_probability(samples: list) -> float:
    """历史同类型炸板后回封比例。samples: list[bool]。"""
    if not samples:
        return 0.0
    return round(sum(1 for s in samples if s) / len(samples), 4)
