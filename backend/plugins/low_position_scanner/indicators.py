"""16 低位涨停选股器指标纯函数（修复版：AND-OR 低位 + 负向动能因子）。"""
import math
from typing import Optional, Tuple


def _float(value, default=None):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def _closes(values: list) -> list:
    out = []
    for v in values or []:
        f = _float(v)
        if f is not None and f > 0:
            out.append(f)
    return out


def pullback_from_high(closes: list, window: int = 60) -> Optional[float]:
    """距近 window 日最高收盘价的回撤幅度（负值，如 -0.30 = -30%）。"""
    vals = _closes(closes)
    if len(vals) < window:
        return None
    high = max(vals[-window:])
    last = vals[-1]
    return round((last - high) / high, 4) if high > 0 else None


def price_percentile(closes: list, window: int = 120) -> Optional[float]:
    """现价在近 window 日价格区间的百分位（0-1，0=最低，1=最高）。"""
    vals = _closes(closes)
    if len(vals) < window:
        return None
    win = vals[-window:]
    lo, hi = min(win), max(win)
    last = win[-1]
    if hi <= lo:
        return 0.5
    return round((last - lo) / (hi - lo), 4)


def is_below_ma(closes: list, period: int = 20) -> Optional[bool]:
    """现价是否在 MA{period} 下方。"""
    vals = _closes(closes)
    if len(vals) < period:
        return None
    return vals[-1] < sum(vals[-period:]) / period


def is_low_position(closes: list, pullback_min: float = -0.25,
                    percentile_max: float = 0.30, ma_period: int = 20) -> Tuple[bool, dict]:
    """低位判断（AND-OR）：回撤必须满足，另两维（百分位/MA20）满足其一。"""
    pb = pullback_from_high(closes, 60)
    pp = price_percentile(closes, 120)
    bm = is_below_ma(closes, ma_period)
    detail = {"pullback_pct": pb, "price_percentile": pp, "below_ma20": bm}
    if pb is None:
        return False, detail
    pullback_ok = pb <= pullback_min
    second_ok = (pp is not None and pp <= percentile_max) or (bm is True)
    return pullback_ok and second_ok, detail


def accel_down_factor(closes: list, window: int = 5) -> float:
    """近 window 日加速下跌惩罚因子（0.0-0.20），不作硬性剔除。"""
    vals = _closes(closes)
    if len(vals) < window + 1:
        return 0.0
    days = vals[-(window + 1):]
    down_count = sum(1 for i in range(1, len(days)) if days[i] < days[i - 1])
    return round(down_count / window * 0.20, 4)


def compute_low_score(pullback_pct, price_pct, zt_count, accel_penalty, weights: dict = None) -> float:
    """综合低位分 0-100：回撤(45%)+百分位(30%)+涨停质量(25%)-动能惩罚。"""
    w = weights or {"pullback": 0.45, "percentile": 0.30, "zt": 0.25}
    pb = _float(pullback_pct)
    pp = _float(price_pct)
    pb_norm = min(abs(pb) / 0.60, 1.0) if pb else 0.0
    pp_norm = max(1.0 - (pp / 0.30), 0.0) if pp is not None else 0.5
    zt_norm = min(_float(zt_count, 0) or 0, 5) / 5.0
    raw = pb_norm * w["pullback"] + pp_norm * w["percentile"] + zt_norm * w["zt"]
    return round(max(0.0, min(1.0, raw - _float(accel_penalty, 0))) * 100, 2)
