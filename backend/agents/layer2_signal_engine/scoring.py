"""多因子加权评分引擎"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from .skills.base import SkillResult
from .skills import build_skills, BaseSkill


@dataclass
class ScoredStock:
    """单只股票的完整评分结果"""
    code: str
    name: str
    zt_date: str
    ref_price: float
    current_price: float
    drop_pct: float
    factor_scores: Dict[str, SkillResult] = field(default_factory=dict)
    total_score: float = 0.0          # 加权总分 (0-100)
    event_impact: float = 0.0         # 事件加减分
    adjusted_score: float = 0.0       # 调整后总分
    rank: int = 0
    recommendation: str = "PASS"      # STRONG_BUY/BUY/WATCH/PASS
    extra: Dict[str, Any] = field(default_factory=dict)


class MultiFactorScorer:
    """多因子加权评分器"""

    def __init__(self, skills: Optional[List[BaseSkill]] = None,
                 thresholds: Optional[Dict[str, float]] = None):
        self.skills = skills or build_skills()
        self.thresholds = thresholds or {
            "strong_buy": 70.0,
            "buy": 55.0,
            "watch": 40.0,
        }

    def score_stock(self, ctx: Dict[str, Any]) -> ScoredStock:
        """对单只股票计算所有因子得分"""
        results: Dict[str, SkillResult] = {}
        total_weight = 0.0
        weighted_sum = 0.0
        event_impact = 0.0

        for skill in self.skills:
            r = skill.score(ctx)
            results[skill.key] = r
            if skill.key == "event_bonus":
                event_impact = r.score
            else:
                weighted_sum += r.score * r.weight
                total_weight += r.weight

        # 归一化到 0-100
        if total_weight > 0:
            total_score = (weighted_sum / total_weight) * 10.0
        else:
            total_score = 0.0

        adjusted = self.clamp(total_score + event_impact, 0, 100)
        recommendation = self._classify(adjusted)

        return ScoredStock(
            code=ctx.get("code", ""),
            name=ctx.get("name", ""),
            zt_date=ctx.get("zt_date", ""),
            ref_price=ctx.get("ref_price", 0.0),
            current_price=ctx.get("current_price", 0.0),
            drop_pct=ctx.get("drop_pct", 0.0),
            factor_scores=results,
            total_score=round(total_score, 1),
            event_impact=round(event_impact, 1),
            adjusted_score=round(adjusted, 1),
            recommendation=recommendation,
            extra=ctx.get("extra", {}),
        )

    def rank(self, stocks: List[ScoredStock]) -> List[ScoredStock]:
        """排序并分配排名"""
        ranked = sorted(stocks, key=lambda x: x.adjusted_score, reverse=True)
        for i, s in enumerate(ranked):
            s.rank = i + 1
        return ranked

    def _classify(self, score: float) -> str:
        if score >= self.thresholds["strong_buy"]:
            return "STRONG_BUY"
        if score >= self.thresholds["buy"]:
            return "BUY"
        if score >= self.thresholds["watch"]:
            return "WATCH"
        return "PASS"

    @staticmethod
    def clamp(v: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, v))
