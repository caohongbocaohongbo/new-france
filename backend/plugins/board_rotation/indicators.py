"""05 板块轮动指标纯函数。"""
import math

DEFAULT_WEIGHTS = {"change": 1.0, "inflow": 1.0, "zt_count": 2.0, "max_height": 1.0, "zt_ratio": 1.0}


def _float(value, default=None):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def zscore(values: list) -> list:
    """z-score 标准化（std=0 时返回全 0）。"""
    vals = [_float(v) for v in values or []]
    vals = [v if v is not None else 0.0 for v in vals]
    if not vals:
        return []
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    std = math.sqrt(var) if var > 0 else 0.0
    if std == 0:
        return [0.0 for _ in vals]
    return [round((v - mean) / std, 4) for v in vals]


def board_score_from_z(z_change, z_inflow, zt_count, max_height, zt_ratio, weights=None) -> float:
    """板块分 = z(涨幅) + z(资金) + log(1+涨停家数)×w + 最高连板×w + 涨停占比×w。"""
    weights = weights or DEFAULT_WEIGHTS
    return round(
        _float(z_change, 0) or 0
        + (_float(z_inflow, 0) or 0)
        + math.log1p(_float(zt_count, 0) or 0) * weights.get("zt_count", 2.0)
        + (_float(max_height, 0) or 0) * weights.get("max_height", 1.0)
        + (_float(zt_ratio, 0) or 0) * weights.get("zt_ratio", 1.0),
        4,
    )


def classify_stage(score, prev_score=None, zt_count=0, prev_zt_count=0) -> str:
    """题材阶段：启动/发酵/高潮/分歧/退潮。"""
    if prev_score is not None and prev_score - score >= 1.0 and zt_count < prev_zt_count:
        return "退潮"
    if zt_count > 0 and prev_zt_count > 0 and zt_count < prev_zt_count:
        return "分歧"
    if score >= 2.0:
        return "高潮"
    if score >= 0.5:
        return "发酵"
    return "启动"


def mainline_confirm(zt_count_today, zt_count_yesterday, in_top_today=True, in_top_yesterday=True) -> bool:
    """主线确认：连续 2 天在榜且涨停家数不衰减。"""
    if not (in_top_today and in_top_yesterday):
        return False
    return zt_count_today >= zt_count_yesterday
