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

        if 5.0 <= to <= 8.0:
            s, detail = 10.0, f"换手率={to:.1f}%，温和放量，理想区间"
        elif 4.0 <= to < 5.0:
            s = self.linear_score(to, 4.0, 5.0, floor=4, ceil=10)
            detail = f"换手率={to:.1f}%，略低于目标"
        elif 8.0 < to <= 10.0:
            s = 7.0
            detail = f"换手率={to:.1f}%，偏高，注意游资痕迹"
        elif 3.0 <= to < 4.0:
            s = 4.0
            detail = f"换手率={to:.1f}%，偏低，交投不够活跃"
        else:
            s, detail = 0.0, f"换手率={to:.1f}%，不符合策略要求"

        return SkillResult(
            name=self.name, key=self.key, score=round(s, 1),
            weight=self.weight, detail=detail, passed=s >= 1.0,
        )
