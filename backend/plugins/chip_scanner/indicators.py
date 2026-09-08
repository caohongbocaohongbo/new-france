"""21 筹码集中度与获利盘选股指标纯函数（无前视）。"""
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


def concentration_ratio(distribution: list) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """90% 成本区间集中度 = (p95 - p05) / p50（越小越集中）。

    返回 (concentration_ratio, p05, p50, p95)，数据不足返回 (None, None, None, None)。
    distribution: [{price_level, volume, ...}]（Volume Profile 分价分布）。
    """
    if not distribution:
        return None, None, None, None
    sorted_dist = sorted(distribution, key=lambda r: r["price_level"])
    total = sum(r["volume"] for r in sorted_dist)
    if total <= 0:
        return None, None, None, None
    cum = 0.0
    p05 = p50 = p95 = None
    for r in sorted_dist:
        cum += r["volume"]
        if p05 is None and cum >= total * 0.05:
            p05 = r["price_level"]
        if p50 is None and cum >= total * 0.50:
            p50 = r["price_level"]
        if p95 is None and cum >= total * 0.95:
            p95 = r["price_level"]
    if p05 is None or p50 is None or p95 is None or p50 == 0:
        return None, None, None, None
    return round((p95 - p05) / p50, 4), p05, p50, p95


def profit_ratio(distribution: list, current_price) -> Optional[float]:
    """获利盘 = 现价下方成本量 / 总成本量（近似）。"""
    price = _float(current_price)
    if not distribution or price is None:
        return None
    total = sum(r["volume"] for r in distribution)
    below = sum(r["volume"] for r in distribution if r["price_level"] <= price)
    return round(below / total, 4) if total > 0 else None


def ma20_slope(closes: list) -> Optional[float]:
    """MA20 斜率（G2：窗口 closes[-26:-6]，off-by-one 修正，取 20 根，终点是 5 日前收盘）。

    返回 (当前 MA20 - 5日前 MA20) / 5日前 MA20（>0 表示 MA20 上行）。
    """
    vals = _closes(closes)
    if len(vals) < 26:
        return None
    ma_now = sum(vals[-20:]) / 20
    ma_prev = sum(vals[-26:-6]) / 20  # G2：5 日前 MA20（20 根，终点是 5 日前收盘）
    return round((ma_now - ma_prev) / ma_prev, 4) if ma_prev > 0 else None


def is_chip_hit(concentration, profit, trend_up, concentration_max: float = 0.15,
                profit_min: float = 0.70) -> bool:
    """AND 三条全过：集中度高(<0.15) + 获利盘>70% + 趋势向上（MA20 上行）。"""
    return (concentration is not None and concentration < concentration_max
            and profit is not None and profit > profit_min
            and trend_up)


def is_tight_control(concentration, profit, concentration_max: float = 0.10,
                     profit_min: float = 0.85) -> bool:
    """高度控盘：集中度<0.10 + 获利盘>85%。"""
    return (concentration is not None and concentration < concentration_max
            and profit is not None and profit > profit_min)


def compute_chip_score(concentration, profit, trend_strength) -> float:
    """综合分 0-100：集中度 40% + 获利盘 40% + 趋势强度 20%。"""
    c_str = 0.0
    if concentration is not None:
        c_str = min(1.0, max(0.0, (0.15 - concentration) / 0.15))  # 0.15→0分，0→满分
    p_str = 0.0
    if profit is not None:
        p_str = min(1.0, max(0.0, (profit - 0.70) / 0.20))  # 70%→0分，90%→满分
    t_str = min(1.0, max(0.0, (trend_strength or 0) / 0.05))  # MA20 斜率 5% 封顶
    return round(c_str * 40 + p_str * 40 + t_str * 20, 2)
