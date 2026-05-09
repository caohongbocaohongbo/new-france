"""流通市值因子 — 权重 10%"""
from .base import BaseSkill, SkillResult
from typing import Any, Dict


class MarketCapSkill(BaseSkill):
    name = "流通市值"
    key = "market_cap"
    weight = 0.10

    def score(self, ctx: Dict[str, Any]) -> SkillResult:
        mc = ctx.get("流通市值")
        if mc is None or mc <= 0:
            return SkillResult(name=self.name, key=self.key, score=0,
                               weight=self.weight, detail="流通市值数据缺失", passed=False)

        mc_yi = mc / 1e8  # 转为亿

        if 50 <= mc_yi <= 100:
            s, detail = 10.0, f"流通市值{mc_yi:.0f}亿，典型中小盘，弹性佳"
        elif 100 < mc_yi <= 150:
            s = 8.0
            detail = f"流通市值{mc_yi:.0f}亿，偏大盘但可接受"
        elif 150 < mc_yi <= 200:
            s = 5.0
            detail = f"流通市值{mc_yi:.0f}亿，接近上限"
        elif 30 <= mc_yi < 50:
            s = 6.0
            detail = f"流通市值{mc_yi:.0f}亿，略低于目标区间"
        elif 200 < mc_yi <= 300:
            s = 2.0
            detail = f"流通市值{mc_yi:.0f}亿，超出上限，弹性不足"
        else:
            s, detail = 0.0, f"流通市值{mc_yi:.0f}亿，严重偏离{50}-{200}亿目标区间"

        return SkillResult(
            name=self.name, key=self.key, score=round(s, 1),
            weight=self.weight, detail=detail, passed=s >= 1.0,
        )
