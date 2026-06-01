"""量比因子 — 权重 8%"""
from .base import BaseSkill, SkillResult
from typing import Any, Dict


class VolumeRatioSkill(BaseSkill):
    name = "量比"
    key = "volume_ratio"
    weight = 0.08

    def score(self, ctx: Dict[str, Any]) -> SkillResult:
        vr = ctx.get("量比")
        if vr is None or vr <= 0:
            return SkillResult(name=self.name, key=self.key, score=0,
                               weight=self.weight, detail="量比数据缺失", passed=False)

        params = ctx.get("strategy_params") or {}
        hard_min = float(params.get("volMin", 1.0) or 1.0)
        hard_max = float(params.get("volMax", 5.0) or 5.0)
        ideal_min = hard_min + (hard_max - hard_min) * 0.125
        ideal_max = hard_min + (hard_max - hard_min) * 0.5

        if ideal_min <= vr <= ideal_max:
            s, detail = 10.0, f"量比={vr:.1f}，温和放量，最佳换手区间"
        elif hard_min <= vr < ideal_min:
            s = self.linear_score(vr, hard_min, ideal_min, floor=5, ceil=10)
            detail = f"量比={vr:.1f}，偏低但尚可"
        elif ideal_max < vr <= hard_max:
            s = self.linear_score(vr, ideal_max, hard_max, floor=5, ceil=10)
            detail = f"量比={vr:.1f}，略高，注意追高风险"
        else:
            s, detail = 0.0, f"量比={vr:.1f}，超出合理范围"

        return SkillResult(
            name=self.name, key=self.key, score=round(s, 1),
            weight=self.weight, detail=detail, passed=s >= 1.0,
        )
