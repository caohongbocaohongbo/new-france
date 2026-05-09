"""回撤幅度因子 — 权重 15%"""
from .base import BaseSkill, SkillResult
from typing import Any, Dict


class PullbackSkill(BaseSkill):
    name = "回撤幅度"
    key = "pullback"
    weight = 0.15

    def score(self, ctx: Dict[str, Any]) -> SkillResult:
        drop_pct = abs(ctx.get("drop_pct", 0))
        ideal_min = 5.0
        ideal_max = 8.0
        hard_max = 10.0

        if drop_pct < 3.0:
            s, detail = 0.0, f"回撤{drop_pct:.1f}%不足3%，尚未进入目标区间"
        elif drop_pct < ideal_min:
            s = self.linear_score(drop_pct, 3.0, ideal_min, floor=0, ceil=7)
            detail = f"回撤{drop_pct:.1f}%，接近目标区间(5-8%)"
        elif drop_pct <= ideal_max:
            s = 10.0
            detail = f"回撤{drop_pct:.1f}%，完美落入5-8%目标区间"
        elif drop_pct <= hard_max:
            s = self.linear_score(drop_pct, ideal_max, hard_max, floor=5, ceil=10)
            detail = f"回撤{drop_pct:.1f}%，略超8%，仍在可接受范围"
        else:
            s, detail = 0.0, f"回撤{drop_pct:.1f}%超过10%上限，风险过大"

        return SkillResult(
            name=self.name, key=self.key, score=round(s, 1),
            weight=self.weight, detail=detail, passed=s >= 1.0,
        )
