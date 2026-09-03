"""19 趋势强度选股指标纯函数（MA 多头 + 创新高 + 量比，无前视）。"""
import math
from typing import Optional, Tuple


def _float(value, default=None):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def _closes(values) -> list:
    out = []
    for v in values or []:
        f = _float(v)
        if f is not None and f > 0:
            out.append(f)
    return out


def _mean(values: list):
    vals = _closes(values)
    return sum(vals) / len(vals) if vals else None


def ma_values(closes: list) -> dict:
    """MA5/MA10/MA20/MA60 均线值（数据不足返回空）。"""
    vals = _closes(closes)
    return {
        "ma5": _mean(vals[-5:]) if len(vals) >= 5 else None,
        "ma10": _mean(vals[-10:]) if len(vals) >= 10 else None,
        "ma20": _mean(vals[-20:]) if len(vals) >= 20 else None,
        "ma60": _mean(vals[-60:]) if len(vals) >= 60 else None,
    }


def is_ma_aligned(closes: list, tol: float = 0.005) -> bool:
    """MA 多头排列：MA5 > MA10 > MA20 > MA60（含容差）。"""
    ma = ma_values(closes)
    if None in (ma["ma5"], ma["ma10"], ma["ma20"], ma["ma60"]):
        return False
    return (ma["ma5"] > ma["ma10"] * (1 - tol)
            and ma["ma10"] > ma["ma20"] * (1 - tol)
            and ma["ma20"] > ma["ma60"] * (1 - tol))


def is_new_high(closes: list, window: int = 60) -> Tuple[bool, Optional[float]]:
    """创 window 日新高：现价 > max(closes[-(window+1):-1])。返回 (命中, 前高)。"""
    vals = _closes(closes)
    if len(vals) < window + 1:
        return False, None
    prev_high = max(vals[-(window + 1):-1])
    return vals[-1] > prev_high, prev_high


def volume_ratio(vols: list, window: int = 5):
    """量比 = 今日量 / 近 window 日均量。"""
    vals = _closes(vols)
    if len(vals) < window + 1:
        return None
    avg = sum(vals[-(window + 1):-1]) / window
    return vals[-1] / avg if avg > 0 else 1.0


def compute_trend_score(closes: list, vols: list, prev_high: float, ma60: float, vr: float) -> float:
    """趋势强度分 0-100：均线乖离 50% + 新高幅度 30% + 量比 20%。"""
    last = _closes(closes)[-1] if closes else None
    ma_str = 0.0
    if last is not None and ma60 and ma60 > 0:
        ma_str = min(1.0, max(0.0, (last - ma60) / ma60 / 0.30))  # 乖离 30% 封顶
    nh_str = 0.0
    if last is not None and prev_high and prev_high > 0:
        nh_str = min(1.0, max(0.0, (last - prev_high) / prev_high / 0.10))  # 新高 10% 封顶
    vr_str = 0.0
    if vr is not None:
        vr_str = min(1.0, max(0.0, (vr - 1.0) / 1.5))  # 量比 2.5 封顶
    return round(ma_str * 50 + nh_str * 30 + vr_str * 20, 2)
