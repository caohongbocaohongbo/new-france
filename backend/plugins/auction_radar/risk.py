"""28 低位竞价雷达风险：阶段硬过滤/软惩罚（挂单压力衰减）。"""
from __future__ import annotations

from . import factors as _factors


def hard_exclude(item, quality, *, allow_stale=False):
    """硬风险：数据质量 INVALID 直接排除正式榜单，保留审计原因。"""
    reasons = []
    if quality and quality == "INVALID":
        reasons.append("quality=INVALID")
    name = str(item.get("name") or "")
    if "ST" in name.upper() or "退" in name:
        reasons.append("ST/退市")
    code = str(item.get("code") or "").zfill(6)
    if code.startswith(("300", "301", "688")):
        reasons.append("非沪深主板")
    return bool(reasons), reasons


def pressure_decay_penalty(frames, drop_pct=0.5, penalty=25.0):
    """挂单压力衰减 -> 软风险扣分（0-100 内），不作唯一硬过滤。"""
    if _factors.orderbook_pressure_decay(frames, drop_pct):
        return penalty, ["挂单压力衰减"]
    return 0.0, []


def high_open_exhaustion_penalty(gap_pct, vol_ratio, gap_exhaust=9.0, vol_ratio_weak=2.0, penalty=30.0):
    """高开无量（gap>gap_exhaust 且量比<vol_ratio_weak）-> 软风险扣分；不用统一 gap>9% 作所有证券硬规则。"""
    g = _factors._float(gap_pct)
    v = _factors._float(vol_ratio)
    if g is not None and g > gap_exhaust and (v is None or v < vol_ratio_weak):
        return penalty, ["高开无量"]
    return 0.0, []
