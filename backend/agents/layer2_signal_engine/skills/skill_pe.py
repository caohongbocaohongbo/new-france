"""市盈率因子 — 权重 8%"""
from .base import BaseSkill, SkillResult
from typing import Any, Dict


class PESkill(BaseSkill):
    name = "市盈率"
    key = "pe"
    weight = 0.08

    def score(self, ctx: Dict[str, Any]) -> SkillResult:
        pe = ctx.get("市盈率")
        if pe is None or pe <= 0:
            return SkillResult(name=self.name, key=self.key, score=5.0,
                               weight=self.weight, detail="PE数据缺失，给中性分", passed=True)

        params = ctx.get("strategy_params") or {}
        max_pe = float(params.get("peMax", 50.0) or 50.0)
        low_pe = min(20.0, max_pe * 0.4)
        mid_pe = min(30.0, max_pe * 0.6)
        high_pe = min(40.0, max_pe * 0.8)

        if pe <= low_pe:
            s, detail = 10.0, f"PE={pe:.1f}，估值优势明显"
        elif pe <= mid_pe:
            s = 8.0
            detail = f"PE={pe:.1f}，合理估值区间"
        elif pe <= high_pe:
            s = 5.0
            detail = f"PE={pe:.1f}，估值偏高"
        elif pe <= max_pe:
            s = 2.0
            detail = f"PE={pe:.1f}，接近上限{max_pe:.0f}"
        else:
            s, detail = 0.0, f"PE={pe:.1f}，超过{max_pe:.0f}倍上限"

        return SkillResult(
            name=self.name, key=self.key, score=round(s, 1),
            weight=self.weight, detail=detail, passed=s >= 1.0,
        )
