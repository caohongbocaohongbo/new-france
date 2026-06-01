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
        params = ctx.get("strategy_params") or {}
        hard_min = float(params.get("mcMin", 50.0) or 50.0)
        hard_max = float(params.get("mcMax", 200.0) or 200.0)
        span = max(1.0, hard_max - hard_min)
        ideal_max = hard_min + span / 3
        mid_max = hard_min + span * 2 / 3

        if hard_min <= mc_yi <= ideal_max:
            s, detail = 10.0, f"流通市值{mc_yi:.0f}亿，典型中小盘，弹性佳"
        elif ideal_max < mc_yi <= mid_max:
            s = 8.0
            detail = f"流通市值{mc_yi:.0f}亿，偏大盘但可接受"
        elif mid_max < mc_yi <= hard_max:
            s = 5.0
            detail = f"流通市值{mc_yi:.0f}亿，接近上限"
        elif max(0.0, hard_min * 0.6) <= mc_yi < hard_min:
            s = 6.0
            detail = f"流通市值{mc_yi:.0f}亿，略低于目标区间"
        elif hard_max < mc_yi <= hard_max * 1.5:
            s = 2.0
            detail = f"流通市值{mc_yi:.0f}亿，超出上限，弹性不足"
        else:
            s, detail = 0.0, f"流通市值{mc_yi:.0f}亿，严重偏离{hard_min:.0f}-{hard_max:.0f}亿目标区间"

        return SkillResult(
            name=self.name, key=self.key, score=round(s, 1),
            weight=self.weight, detail=detail, passed=s >= 1.0,
        )
