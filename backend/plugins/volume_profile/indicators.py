"""11 分价成交（Volume Profile 近似）纯函数。"""
import math


def _float(value, default=None):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def round_to_step(price, step: float) -> float:
    return round(round(float(price) / float(step)) * float(step), 4)


def vwap(bars: list):
    """VWAP = Σ成交额 / Σ成交量（×100 手）。"""
    total_amount = 0.0; total_vol = 0.0
    for b in bars or []:
        total_amount += _float(b.get("amount"), 0) or 0
        total_vol += _float(b.get("vol"), 0) or 0
    if total_vol <= 0:
        return None
    return round(total_amount / (total_vol * 100), 4)


def allocate_bar(bar: dict, step: float = 0.05) -> dict:
    """分钟量近似分配：close 70%、high-low 区间 30%（均匀）。"""
    close = _float(bar.get("close")); high = _float(bar.get("high")); low = _float(bar.get("low"))
    vol = _float(bar.get("vol"), 0) or 0
    if close is None or high is None or low is None or vol <= 0:
        return {}
    levels = {round_to_step(close, step): vol * 0.7}
    span = high - low
    if span <= 0:
        return levels
    n = max(1, int(span / float(step)))
    share = vol * 0.3 / n
    for i in range(n):
        p = round_to_step(low + i * float(step), step)
        levels[p] = levels.get(p, 0.0) + share
    return levels


def build_price_distribution(bars: list, step: float = 0.05) -> list:
    """分价分布：[{price_level, volume, cumulative_ratio}]。"""
    agg = {}
    for b in bars or []:
        for level, vol in allocate_bar(b, step).items():
            agg[level] = agg.get(level, 0.0) + vol
    total = sum(agg.values())
    if total <= 0:
        return []
    cum = 0.0
    rows = []
    for level in sorted(agg.keys()):
        cum += agg[level]
        rows.append({"price_level": level, "volume": round(agg[level], 2), "cumulative_ratio": round(cum / total, 4)})
    return rows


def poc_price(distribution: list):
    """最大成交量价格。"""
    if not distribution:
        return None
    return max(distribution, key=lambda r: r["volume"])["price_level"]


def profit_ratio(distribution: list, current_price):
    """获利盘比例 = 现价下方成交量 / 总成交量（近似）。"""
    price = _float(current_price)
    if price is None or not distribution:
        return None
    total = sum(r["volume"] for r in distribution)
    below = sum(r["volume"] for r in distribution if r["price_level"] <= price)
    return round(below / total, 4) if total > 0 else None


def main_cost_band(distribution: list, top_ratio: float = 0.2) -> dict:
    """主力成本带：大单量 Top 价格档的加权均值（近似）。"""
    if not distribution:
        return {"low": None, "high": None, "weighted": None}
    ordered = sorted(distribution, key=lambda r: r["volume"], reverse=True)
    top_n = max(1, int(len(ordered) * top_ratio))
    top = ordered[:top_n]
    total = sum(r["volume"] for r in top)
    weighted = sum(r["price_level"] * r["volume"] for r in top) / total if total > 0 else None
    prices = sorted(r["price_level"] for r in top)
    return {"low": prices[0], "high": prices[-1], "weighted": round(weighted, 4) if weighted else None}
