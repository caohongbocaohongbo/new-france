"""回撤幅度因子 — 权重 15%"""
from .base import BaseSkill, SkillResult
from typing import Any, Dict


class PullbackSkill(BaseSkill):
    name = "回撤幅度"
    key = "pullback"
    weight = 0.15

    def score(self, ctx: Dict[str, Any]) -> SkillResult:
        drop_pct = abs(ctx.get("drop_pct", 0))
        params = ctx.get("strategy_params") or {}
        hard_min = float(params.get("dropMin", 3.0) or 3.0)
        ideal_min = float(params.get("dropMin", 5.0) or 5.0)
        hard_max = float(params.get("dropMax", 10.0) or 10.0)
        ideal_max = min(hard_max, ideal_min + max(1.0, (hard_max - ideal_min) * 0.6))

        if drop_pct < hard_min:
            s, detail = 0.0, f"回撤{drop_pct:.1f}%不足{hard_min:.1f}%，尚未进入目标区间"
        elif drop_pct < ideal_min:
            s = self.linear_score(drop_pct, hard_min, ideal_min, floor=0, ceil=7)
            detail = f"回撤{drop_pct:.1f}%，接近目标区间({ideal_min:.1f}-{ideal_max:.1f}%)"
        elif drop_pct <= ideal_max:
            s = 10.0
            detail = f"回撤{drop_pct:.1f}%，落入{ideal_min:.1f}-{ideal_max:.1f}%目标区间"
        elif drop_pct <= hard_max:
            s = self.linear_score(drop_pct, ideal_max, hard_max, floor=5, ceil=10)
            detail = f"回撤{drop_pct:.1f}%，略超目标区间，仍在可接受范围"
        else:
            s, detail = 0.0, f"回撤{drop_pct:.1f}%超过{hard_max:.1f}%上限，风险过大"

        return SkillResult(
            name=self.name, key=self.key, score=round(s, 1),
            weight=self.weight, detail=detail, passed=s >= 1.0,
        )
