"""04 尾盘抢筹指标纯函数。"""
import math


def _float(value, default=None):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def _clip(value, low, high):
    return max(low, min(high, value))


def tail_acceleration(early_change_pct, late_change_pct):
    """尾盘加速 = 14:50 快照涨幅 - 14:20 快照涨幅。"""
    e = _float(early_change_pct); l = _float(late_change_pct)
    if e is None or l is None:
        return None
    return round(l - e, 4)


def tail_fund_strength(main_inflow_30m, amount):
    """尾盘资金强度 = 尾盘30分钟主力净流入 / 成交额。"""
    inflow = _float(main_inflow_30m); amt = _float(amount)
    if inflow is None or amt is None or amt <= 0:
        return None
    return round(inflow / amt, 4)


def tail_raid_score(change_pct, volume_ratio, main_inflow_ratio, acceleration=None):
    """尾盘分 = 涨幅分 + 量能分 + 资金分 + 加速分（0-100）。"""
    change = _float(change_pct, 0) or 0
    vr = _float(volume_ratio, 1) or 1
    ratio = _float(main_inflow_ratio, 0) or 0
    score = 0.0
    score += _clip((change - 3.0) / 5.0 * 40.0, 0, 40)
    score += _clip((vr - 1.0) * 15.0, 0, 20)
    score += _clip(ratio / 2.0, 0, 30)
    if acceleration is not None:
        score += _clip(acceleration * 10.0, 0, 10)
    return round(min(100.0, score), 2)


def is_tail_candidate(change_pct, is_limit_up=False, change_min=3.0, change_max=8.0):
    """温和放量未涨停：涨幅 3%-8% 且未涨停。"""
    change = _float(change_pct)
    if change is None or bool(is_limit_up):
        return False
    return change_min <= change <= change_max
