"""盘中雷达指标纯函数。"""
import math
from datetime import datetime, timedelta
from typing import Optional


def _float(value, default: Optional[float] = None) -> Optional[float]:
    if value is None or value == "" or value == "-":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    return None if value is None else round(value, digits)


def order_book_strength(quote: dict) -> dict:
    """指标11：五档买卖盘强弱。"""
    bid_amt = 0.0
    ask_amt = 0.0
    for idx in range(1, 6):
        bid_amt += (_float(quote.get(f"bid{idx}"), 0) or 0) * (_float(quote.get(f"bid_vol{idx}"), 0) or 0)
        ask_amt += (_float(quote.get(f"ask{idx}"), 0) or 0) * (_float(quote.get(f"ask_vol{idx}"), 0) or 0)
    total = bid_amt + ask_amt
    strength = None if total <= 0 else 100.0 * bid_amt / total
    return {
        "bid_amt": _round(bid_amt, 2),
        "ask_amt": _round(ask_amt, 2),
        "strength_amt": _round(strength, 2),
    }


def active_buy_ratio(quote: dict) -> Optional[float]:
    """主动买占比：b_vol/(b_vol+s_vol)。"""
    buy_vol = _float(quote.get("b_vol"), 0) or 0
    sell_vol = _float(quote.get("s_vol"), 0) or 0
    total = buy_vol + sell_vol
    if total <= 0:
        return None
    return _round(buy_vol / total, 4)


def main_fund_metrics(pool_item: dict) -> dict:
    """指标3/4：观察池提供的大单净流入和大单买入占比。"""
    net = _float(pool_item.get("main_net_inflow"))
    if net is None:
        # principal_capital 三个源的字段名均为 super_net/big_net（不带 _inflow 后缀）
        super_net = _float(pool_item.get("super_net"), 0) or 0
        big_net = _float(pool_item.get("big_net"), 0) or 0
        net = super_net + big_net
    return {
        "main_net_inflow": _round(net, 2),
        "main_inflow_ratio": _round(_float(pool_item.get("main_inflow_ratio")), 4),
        "r1_in": _round(_float(pool_item.get("r1_in")), 2),
        "r1_out": _round(_float(pool_item.get("r1_out")), 2),
    }


def large_order_amounts(pool_item: dict) -> dict:
    """指标1/2：新浪源提供的当日累计大单主动买入/卖出额。"""
    return {
        "large_buy_amount": _round(_float(pool_item.get("r1_in")), 2),
        "large_sell_amount": _round(_float(pool_item.get("r1_out")), 2),
    }


def _valid_bars(bars, require_volume: bool = False) -> list:
    """过滤缺失收盘价；仅在成交量计算时过滤集合竞价伪 bar。"""
    valid = []
    for bar in bars or []:
        if not isinstance(bar, dict):
            continue
        vol = _float(bar.get("vol"), 0) or 0
        if (require_volume and vol < 1) or _float(bar.get("close")) is None:
            continue
        valid.append(bar)
    return valid


def price_returns(bars_by_category: dict) -> dict:
    """指标6：按 TDX 周期编号计算 1/5/15 分钟收益率。"""
    result = {}
    for key, label in ((8, "return_1m"), (0, "return_5m"), (1, "return_15m")):
        bars = _valid_bars(bars_by_category.get(key) or bars_by_category.get(str(key)) or [])
        if len(bars) < 2:
            result[label] = None
            continue
        current = _float(bars[-1].get("close"))
        previous = _float(bars[-2].get("close"))
        result[label] = _round((current - previous) / previous, 4) if previous else None
    return result


def vwap_deviation(price, bars) -> Optional[float]:
    """指标7：价格相对成交量加权均价的偏离。"""
    valid = _valid_bars(bars, require_volume=True)
    total_vol = sum(_float(bar.get("vol"), 0) or 0 for bar in valid)
    total_amount = sum(_float(bar.get("amount"), 0) or 0 for bar in valid)
    vwap = total_amount / (total_vol * 100) if total_vol > 0 else None
    current = _float(price)
    return _round((current - vwap) / vwap, 4) if current is not None and vwap else None


def distance_from_high(price, bars) -> Optional[float]:
    """指标8：当前价距离有效 bar 最高价的比例。"""
    valid = [bar for bar in _valid_bars(bars) if _float(bar.get("high")) is not None]
    high = max((_float(bar.get("high")) for bar in valid), default=None)
    current = _float(price)
    return _round((high - current) / high, 4) if high and current is not None else None


def price_impact(change_pct, main_net_inflow, price, liutongguben) -> Optional[float]:
    """指标9：涨跌幅相对主力净流入/流通市值的价格冲击。"""
    change = _float(change_pct)
    inflow = _float(main_net_inflow)
    current = _float(price)
    shares = _float(liutongguben)
    if None in (change, inflow, current, shares) or inflow == 0 or current <= 0 or shares <= 0:
        return None
    denominator = inflow / (current * shares)
    return _round(change / denominator, 4) if denominator else None


def volume_ratio(current_bars, history_by_time: dict) -> Optional[float]:
    """指标10：当前累计成交量/过去 N 日同时间段平均累计成交量。"""
    valid = [
        bar for bar in (current_bars or [])
        if isinstance(bar, dict) and (_float(bar.get("vol"), 0) or 0) >= 1
    ]
    current_total = sum(_float(bar.get("vol"), 0) or 0 for bar in valid)
    current_minutes = set()
    for bar in valid:
        minute = str(bar.get("time") or bar.get("datetime") or "")
        if len(minute) >= 5:
            current_minutes.add(minute[-5:])
    historical_average_total = 0.0
    matched = False
    for minute in current_minutes:
        values = [
            number for number in (_float(value) for value in (history_by_time or {}).get(minute, []))
            if number is not None and number >= 0
        ]
        if values:
            historical_average_total += sum(values) / len(values)
            matched = True
    return _round(current_total / historical_average_total, 4) if matched and historical_average_total > 0 else None


def _slope(values: list) -> float:
    if len(values) < 2:
        return 0.0
    xs = list(range(len(values)))
    mean_x = sum(xs) / len(xs)
    mean_y = sum(values) / len(values)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values)) / denominator if denominator else 0.0


def _xy_slope(points: list) -> float:
    if len(points) < 2:
        return 0.0
    mean_x = sum(point[0] for point in points) / len(points)
    mean_y = sum(point[1] for point in points) / len(points)
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    return sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator if denominator else 0.0


def sell_pressure_decay(quotes: list, minute_buckets: dict, now: datetime,
                        window_s: int = 90, decay_minutes: int = 3) -> dict:
    """指标12：秒级委卖与已收口分钟卖出额的衰减近似。"""
    cutoff = now - timedelta(seconds=max(1, int(window_s)))
    ask_points = []
    for row in quotes or []:
        try:
            ts = datetime.fromisoformat(str(row.get("ts")))
        except (TypeError, ValueError):
            continue
        if ts < cutoff or ts > now:
            continue
        quote = row.get("quote") or {}
        ask_points.append(((ts - cutoff).total_seconds(), sum((_float(quote.get(f"ask_vol{i}"), 0) or 0) for i in range(1, 6))))
    ask_values = [value for _, value in ask_points]
    ask_mean = sum(ask_values) / len(ask_values) if ask_values else 0
    decay_ask = -_xy_slope(ask_points) * max(1, int(window_s)) / ask_mean if ask_mean > 0 else 0.0

    current_minute = now.strftime("%H:%M")
    closed = [
        (_minute, _float(bucket.get("sell_amt"), 0) or 0)
        for _minute, bucket in (minute_buckets or {}).items()
        if _minute < current_minute
    ]
    sell_values = [value for _, value in sorted(closed)[-max(2, int(decay_minutes)):]]
    sell_mean = sum(sell_values) / len(sell_values) if sell_values else 0
    decay_sell = -_slope(sell_values) / sell_mean if sell_mean > 0 else 0.0
    sell_surge = (sell_values[-1] / (sum(sell_values[:-1]) / len(sell_values[:-1])) - 1.0) if len(sell_values) >= 3 and sum(sell_values[:-1]) > 0 else 0.0

    decay_ask = min(1.0, max(0.0, decay_ask))
    decay_sell = min(1.0, max(0.0, decay_sell))
    return {
        "decay_ask": _round(decay_ask, 4),
        "decay_sell": _round(decay_sell, 4),
        "decay_score": _round(0.5 * decay_ask + 0.5 * decay_sell, 4),
        "sell_surge": _round(sell_surge, 4),
    }


# ==== 07 盘口委托失衡与五档动态雷达（纯函数） ====

def bid_ask_imbalance(quote: dict) -> Optional[float]:
    """盘口失衡 = (买五档总额 - 卖五档总额) / (买+卖总额)，范围 [-1, 1]。"""
    book = order_book_strength(quote)
    total = (_float(book.get("bid_amt"), 0) or 0) + (_float(book.get("ask_amt"), 0) or 0)
    if total <= 0:
        return None
    return _round(((_float(book.get("bid_amt"), 0) or 0) - (_float(book.get("ask_amt"), 0) or 0)) / total, 4)


def top_concentration(quote: dict) -> Optional[float]:
    """档位集中度 = max(vol_i) / Σ vol_i（买卖十档）。"""
    vols = [_float(quote.get(f"bid_vol{i}"), 0) or 0 for i in range(1, 6)] + [_float(quote.get(f"ask_vol{i}"), 0) or 0 for i in range(1, 6)]
    total = sum(vols)
    if total <= 0:
        return None
    return _round(max(vols) / total, 4)


def _normalized_slope(values: list) -> float:
    if not values or len(values) < 2:
        return 0.0
    values = [_float(v, 0) or 0 for v in values]
    slope = _slope(values)
    mean = sum(values) / len(values)
    return slope / mean if mean else 0.0


def ask_pressure_decay(series: list) -> float:
    """卖压衰减 = 卖一量最近 K 帧斜率（负 = 撤压/消化），归一化。"""
    return _round(_normalized_slope(series), 4)


def bid_buildup(series: list) -> float:
    """买盘堆积 = 买一量最近 K 帧斜率（正 = 垫单抢筹），归一化。"""
    return _round(_normalized_slope(series), 4)


def withdraw_pressure_signal(vol_series: list, price_series: list = None,
                             drop_pct: float = 0.4) -> bool:
    """卖一量窗口内下降超过 drop_pct 且价格未跌 → 疑似撤压。"""
    if len(vol_series) < 2:
        return False
    first = _float(vol_series[0])
    if first is None or first <= 0:
        return False
    dropped = (first - (_float(vol_series[-1], first) or first)) / first >= drop_pct
    if dropped and price_series and len(price_series) >= 2 and _float(price_series[0]):
        dropped = (_float(price_series[-1], 0) or 0) >= (_float(price_series[0], 0) or 0)
    return dropped


def bid_buildup_signal(vol_series: list, rise_pct: float = 0.5) -> bool:
    """买一量窗口内上升超过 rise_pct → 疑似垫单抢筹。"""
    if len(vol_series) < 2:
        return False
    first = _float(vol_series[0])
    if first is None or first <= 0:
        return False
    return ((_float(vol_series[-1], first) or first) - first) / first >= rise_pct


def fund_persistence_minutes(series: list) -> float:
    """指标5：主力净流入保持非递减的持续分钟数。"""
    points = []
    for item in series or []:
        value = _float(item.get("main_net_inflow"))
        try:
            ts = datetime.fromisoformat(str(item.get("ts")))
        except (TypeError, ValueError):
            continue
        if value is not None:
            points.append((ts, value))
    if len(points) < 2:
        return 0.0
    start = len(points) - 1
    while start > 0 and points[start][1] >= points[start - 1][1]:
        start -= 1
    return _round(max(0.0, (points[-1][0] - points[start][0]).total_seconds() / 60), 2) or 0.0
