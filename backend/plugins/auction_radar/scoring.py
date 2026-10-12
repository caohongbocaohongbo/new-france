"""28 低位竞价雷达评分：缺失重归一化、排名、合成。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class FactorSpec:
    name: str
    normalization_version: str
    direction: str                 # positive | negative | band_pass
    required: bool
    weight: float
    normalize: Callable[[float], Optional[float]] = None

    def norm(self, value: float) -> Optional[float]:
        if self.normalize is not None:
            return self.normalize(value)
        if value is None:
            return None
        return max(0.0, min(100.0, float(value)))


@dataclass
class FactorValue:
    raw_value: Optional[float] = None
    normalized_value: Optional[float] = None
    effective_weight: float = 0.0
    missing_reason: Optional[str] = None
    evidence_fields: list[str] = field(default_factory=list)


def weighted_auction_score(values, weights, *, min_coverage=0.70):
    """按已验证且非空的因子计算 0-100；缺失因子重归一化；coverage 不足返回 (None, coverage)。

    values: {name: 0..100 已归一化分数}；weights: {name: weight}。
    """
    if not weights:
        return None, 0.0
    valid = {k: v for k, v in (values or {}).items()
             if v is not None and k in weights}
    total_weight = sum(weights.values()) or 0.0
    if total_weight <= 0:
        return None, 0.0
    coverage = sum(weights[k] for k in valid) / total_weight
    if coverage < min_coverage:
        return None, round(coverage, 4)
    score = sum(valid[k] * weights[k] for k in valid) / sum(weights[k] for k in valid)
    return round(max(0.0, min(100.0, score)), 2), round(coverage, 4)


def clamp(value, lo=0.0, hi=100.0):
    return max(lo, min(hi, value))


def total_score(low_score, auction_score, risk_penalty=0.0, w_low=0.40, w_auction=0.60):
    """合成总分：low_score 只在这里出现一次；risk 作为扣分。"""
    base = 0.0
    if low_score is not None:
        base += w_low * float(low_score)
    if auction_score is not None:
        base += w_auction * float(auction_score)
    return round(clamp(base - float(risk_penalty or 0.0)), 2)


def build_ranking(items, key="total_score", reverse=True, tiebreak="code"):
    """按 key 排序并写 rank；同分按 tiebreak 确定性排序。"""
    ranked = sorted(
        items or [],
        key=lambda it: (-(float(it.get(key) or 0.0)) if reverse else float(it.get(key) or 0.0),
                        str(it.get(tiebreak) or "")),
    )
    for i, it in enumerate(ranked):
        it["rank"] = i + 1
    return ranked
