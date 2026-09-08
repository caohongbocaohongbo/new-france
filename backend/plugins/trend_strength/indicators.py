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


def ma_series_full(closes: list) -> dict:
    """MA5/10/20/60 全序列（详情副图用，长度 = len(closes)，前 period-1 根为 None）。"""
    vals = _closes(closes)
    L = len(vals)
    out = {"ma5": [None] * L, "ma10": [None] * L, "ma20": [None] * L, "ma60": [None] * L}
    for period, key in ((5, "ma5"), (10, "ma10"), (20, "ma20"), (60, "ma60")):
        if L < period:
            continue
        for i in range(period - 1, L):
            out[key][i] = round(sum(vals[i - period + 1:i + 1]) / period, 4)
    return out


def is_ma_aligned(closes: list) -> bool:
    """MA 多头排列（严格大于，去容差；加 ma5-ma60>0.02 排除横盘）。

    G1 修正：原容差 `ma5 > ma10*(1-0.005)` 在横盘期把均线粘合票全部纳入；
    现改严格大于 + (ma5-ma60)/ma60 > 0.02 排除横盘（均线粘合期命中数减少，提高精度）。
    """
    ma = ma_values(closes)
    if None in (ma["ma5"], ma["ma10"], ma["ma20"], ma["ma60"]):
        return False
    if not (ma["ma5"] > ma["ma10"] > ma["ma20"] > ma["ma60"]):
        return False
    # 排除横盘：ma5 与 ma60 差值 > 2%（均线粘合期不入选）
    return (ma["ma5"] - ma["ma60"]) / ma["ma60"] > 0.02


def is_new_high(closes: list, window: int = 60) -> Tuple[bool, Optional[float]]:
    """创 window 日新高：现价 > max(closes[-(window+1):-1])。返回 (命中, 前高)。"""
    vals = _closes(closes)
    if len(vals) < window + 1:
        return False, None
    prev_high = max(vals[-(window + 1):-1])
    return vals[-1] > prev_high, prev_high


def volume_ratio(vols: list, window: int = 5):
    """量比 = 今日量 / 近 window 日均量（G3：过滤停牌日 vol=0 再取均量）。

    G3 修正：原实现分母含停牌日（vol=0），导致量比虚高；
    现过滤 vol=0 的日期再取近 window 日均量。
    """
    raw = [_float(v) for v in vols or []]
    today_vol = raw[-1] if raw else None
    # 近 window 日量（过滤 vol=0 停牌日）
    prev_vols = [v for v in raw[-(window + 1):-1] if v is not None and v > 0]
    if today_vol is None or not prev_vols:
        return None
    avg = sum(prev_vols) / len(prev_vols)
    return today_vol / avg if avg > 0 else 1.0


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
