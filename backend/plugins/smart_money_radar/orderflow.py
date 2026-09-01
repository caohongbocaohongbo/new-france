"""08 逐笔成交行为分析（分钟级近似，复用 RadarStore.minute_buckets / fresh txs）。"""
import math


def _float(value, default=0.0):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def tx_amount(tx: dict) -> float:
    """单笔成交额 = price * vol * 100。"""
    price = _float(tx.get("price"), 0) or 0
    vol = _float(tx.get("vol"), 0) or 0
    return price * vol * 100


def classify_transactions(txs: list, large_threshold: float = 1_000_000) -> list:
    """逐笔标注方向与大单。side 0 主买 / 1 主卖 / 2 中性。"""
    out = []
    for tx in txs or []:
        amount = tx_amount(tx)
        side = int(tx.get("buyorsell") if tx.get("buyorsell") is not None else 2)
        out.append({
            **tx, "amount": amount, "side": side,
            "is_large": amount >= float(large_threshold),
        })
    return out


def large_order_summary(enriched: list) -> dict:
    """大单主动买/卖金额与笔数。"""
    large_buy = large_sell = 0.0
    buy_count = sell_count = 0
    for tx in enriched or []:
        if not tx.get("is_large"):
            continue
        if tx.get("side") == 0:
            large_buy += tx.get("amount", 0); buy_count += 1
        elif tx.get("side") == 1:
            large_sell += tx.get("amount", 0); sell_count += 1
    return {
        "large_buy": round(large_buy, 2), "large_sell": round(large_sell, 2),
        "large_buy_count": buy_count, "large_sell_count": sell_count,
    }


def continuous_attack_count(enriched: list, same_side: int = 0, window: int = 5, min_count: int = 3) -> int:
    """连续同向攻击窗口数（滑动窗口内连续 min_count 笔同向）。"""
    sides = [tx.get("side") for tx in enriched or []]
    count = 0
    for i in range(len(sides) - int(min_count) + 1):
        if all(s == same_side for s in sides[i:i + int(min_count)]):
            count += 1
    return count


def split_large_flag(enriched: list, large_threshold: float = 1_000_000) -> bool:
    """拆单嫌疑：同分钟同方向多笔小单合计达大单规模。"""
    buckets = {}
    for tx in enriched or []:
        if tx.get("is_large") or tx.get("side") == 2:
            continue
        minute = str(tx.get("time") or "")[:5]
        key = (minute, tx.get("side"))
        buckets[key] = buckets.get(key, 0.0) + tx.get("amount", 0)
    return any(total >= float(large_threshold) for total in buckets.values())


def wash_trade_flag(minute_buckets: dict, neutral_ratio: float = 0.5) -> bool:
    """对倒嫌疑：中性量占比高且买卖净额≈0。"""
    buy = sell = neutral = 0.0
    for bucket in (minute_buckets or {}).values():
        buy += _float(bucket.get("buy_amt"), 0) or 0
        sell += _float(bucket.get("sell_amt"), 0) or 0
        neutral += _float(bucket.get("neutral_amt"), 0) or 0
    total = buy + sell + neutral
    if total <= 0:
        return False
    net_ratio = abs(buy - sell) / total if total > 0 else 1.0
    return (neutral / total) >= float(neutral_ratio) and net_ratio <= 0.1


def summarize_minute(minute_buckets: dict) -> list:
    """把 minute_buckets 汇总为分钟级特征行。"""
    rows = []
    for minute in sorted((minute_buckets or {}).keys()):
        b = minute_buckets[minute]
        rows.append({
            "minute": minute,
            "buy_amt": round(_float(b.get("buy_amt"), 0) or 0, 2),
            "sell_amt": round(_float(b.get("sell_amt"), 0) or 0, 2),
            "neutral_amt": round(_float(b.get("neutral_amt"), 0) or 0, 2),
            "count": int(_float(b.get("count"), 0) or 0),
        })
    return rows
