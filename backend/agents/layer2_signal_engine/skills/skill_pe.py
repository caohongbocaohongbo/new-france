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

        if pe <= 20:
            s, detail = 10.0, f"PE={pe:.1f}，估值优势明显"
        elif pe <= 30:
            s = 8.0
            detail = f"PE={pe:.1f}，合理估值区间"
        elif pe <= 40:
            s = 5.0
            detail = f"PE={pe:.1f}，估值偏高"
        elif pe <= 50:
            s = 2.0
            detail = f"PE={pe:.1f}，接近上限50"
        else:
            s, detail = 0.0, f"PE={pe:.1f}，超过50倍上限"

        return SkillResult(
            name=self.name, key=self.key, score=round(s, 1),
            weight=self.weight, detail=detail, passed=s >= 1.0,
        )
