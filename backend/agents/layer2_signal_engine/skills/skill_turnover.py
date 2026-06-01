"""换手率因子 — 权重 8%"""
from .base import BaseSkill, SkillResult
from typing import Any, Dict


class TurnoverSkill(BaseSkill):
    name = "换手率"
    key = "turnover"
    weight = 0.08

    def score(self, ctx: Dict[str, Any]) -> SkillResult:
        to = ctx.get("换手率")
        if to is None or to <= 0:
            return SkillResult(name=self.name, key=self.key, score=0,
                               weight=self.weight, detail="换手率数据缺失", passed=False)

        params = ctx.get("strategy_params") or {}
        hard_min = float(params.get("turnoverMin", 5.0) or 5.0)
        hard_max = float(params.get("turnoverMax", 10.0) or 10.0)
        ideal_max = hard_min + (hard_max - hard_min) * 0.6
        soft_min = max(0.0, hard_min - 1.0)

        if hard_min <= to <= ideal_max:
            s, detail = 10.0, f"换手率={to:.1f}%，温和放量，理想区间"
        elif soft_min <= to < hard_min:
            s = self.linear_score(to, soft_min, hard_min, floor=4, ceil=10)
            detail = f"换手率={to:.1f}%，略低于目标"
        elif ideal_max < to <= hard_max:
            s = 7.0
            detail = f"换手率={to:.1f}%，偏高，注意游资痕迹"
        elif max(0.0, soft_min - 1.0) <= to < soft_min:
            s = 4.0
            detail = f"换手率={to:.1f}%，偏低，交投不够活跃"
        else:
            s, detail = 0.0, f"换手率={to:.1f}%，不符合策略要求"

        return SkillResult(
            name=self.name, key=self.key, score=round(s, 1),
            weight=self.weight, detail=detail, passed=s >= 1.0,
        )
