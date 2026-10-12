"""28 低位竞价雷达因子纯函数（严格单位，双分支）。"""
import math


def _float(value, default=None):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def auction_gap(price, last_close):
    """竞价涨幅% = (虚拟参考价 - 昨收) / 昨收 * 100。昨收<=0 防除零返回 None。"""
    p = _float(price)
    lc = _float(last_close)
    if p is None or lc is None or lc <= 0:
        return None
    return round((p - lc) / lc * 100, 4)


def match_amount_yuan(price, shares):
    """竞价匹配金额(元) = 虚拟参考价 × 匹配股数。"""
    p = _float(price)
    s = _float(shares)
    if p is None or s is None or p <= 0 or s < 0:
        return None
    return round(p * s, 2)


def auction_turnover_pct(shares, float_shares):
    """竞价换手% = 匹配股数 / 流通股本 × 100。单位必须为「股」。"""
    s = _float(shares)
    fs = _float(float_shares)
    if s is None or fs is None or fs <= 0:
        return None
    return round(s / fs * 100, 4)


def orderbook_imbalance(bid_vol_total, ask_vol_total):
    """五档挂单不平衡 [-1,1]；原始股数差只审计，不直接入分。"""
    b = _float(bid_vol_total)
    a = _float(ask_vol_total)
    if b is None or a is None:
        return None
    denom = b + a
    if denom <= 0:
        return 0.0
    return round((b - a) / denom, 4)


def limit_up_seal_strength(price, limit_up, bid_vol1, bid_vol_series):
    """竞价一字涨停封单强度 0-100；五档买一挂单量是「挂单近似」，非权威涨停未匹配量。"""
    p = _float(price)
    lu = _float(limit_up)
    if p is None or lu is None or lu <= 0:
        return None
    try:
        from backend.agents.layer1_data_collector.sources.quote_contract import at_limit_price
        if at_limit_price(p, lu) is not True:
            return None
    except Exception:
        if p < lu * 0.999:
            return None
    b1 = _float(bid_vol1)
    series = [_float(v, 0) or 0 for v in (bid_vol_series or [])]
    scale = min(b1 / 100000.0, 1.0) if b1 is not None else 0.0
    growth = 0.0
    if len(series) >= 2 and series[0] > 0:
        growth = min(max((series[-1] - series[0]) / series[0], 0.0), 1.0)
    return round((scale * 0.7 + growth * 0.3) * 100, 2)


def orderbook_pressure_decay(frames, drop_pct=0.5):
    """近 K 帧五档买挂单量显著下降(>=drop_pct)且虚拟参考价下移 -> 挂单压力衰减。

    frames: [{bid_vol_total, price}, ...]。只描述可观测现象，不宣称撤单。
    """
    if not frames or len(frames) < 2:
        return False
    bids = [_float(f.get("bid_vol_total"), 0) or 0 for f in frames]
    prices = [_float(f.get("price")) for f in frames]
    if bids[0] <= 0:
        return False
    vol_drop = (bids[0] - bids[-1]) / bids[0] >= drop_pct
    price_down = prices[0] is not None and prices[-1] is not None and prices[-1] < prices[0]
    return vol_drop and price_down


def gap_band_pass(gap_pct, gap_min=3.0, gap_max=7.0, gap_exhaust=9.0):
    """竞价涨幅区间型映射 0-100：gap_min~gap_max 满分，>gap_exhaust 扣分，负值低分。"""
    g = _float(gap_pct)
    if g is None:
        return None
    if gap_min <= g <= gap_max:
        return 100.0
    if g > gap_exhaust:
        return max(0.0, 100.0 - (g - gap_exhaust) * 20.0)
    if gap_max < g <= gap_exhaust:
        # 7~9 之间：线性从 100 降到 80
        return max(0.0, 100.0 - (g - gap_max) * 10.0)
    if 0 <= g < gap_min:
        return round(g / gap_min * 100.0, 2)
    # 负高开
    return max(0.0, 100.0 + g * 5.0)
