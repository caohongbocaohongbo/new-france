"""主力资金占比因子（plugin 独立）。

设计说明：此因子默认**不注册**到主项目的 ALL_SKILLS 列表，避免影响日常多因子打分。
若未来需要让它参与日终打分，在 backend/agents/layer2_signal_engine/skills/__init__.py 中
手动追加 `from backend.plugins.principal_capital.skill import PrincipalCapitalSkill` 即可。
"""
from typing import Any, Dict

from backend.agents.layer2_signal_engine.skills.base import BaseSkill, SkillResult


class PrincipalCapitalSkill(BaseSkill):
    name = "主力资金"
    key = "principal_capital"
    weight = 0.0  # 默认不参与打分

    def score(self, ctx: Dict[str, Any]) -> SkillResult:
        ratio = ctx.get("main_inflow_ratio")
        if ratio is None:
            return SkillResult(
                name=self.name, key=self.key, score=5.0,
                weight=self.weight, detail="资金流数据缺失，给中性分", passed=True,
            )
        ratio = float(ratio)
        score = self.clamp(5.0 + ratio / 10.0, 0.0, 10.0)
        if ratio >= 50:
            detail, passed = f"主力净流入{ratio:.1f}%，强信号", True
        elif ratio >= 20:
            detail, passed = f"主力净流入{ratio:.1f}%，温和流入", True
        elif ratio > -20:
            detail, passed = f"主力净占比{ratio:+.1f}%，中性", True
        elif ratio > -30:
            detail, passed = f"主力净流出{abs(ratio):.1f}%，弱出货", True
        elif ratio > -50:
            detail, passed = f"主力净流出{abs(ratio):.1f}%，明显派发", False
        else:
            detail, passed = f"主力净流出{abs(ratio):.1f}%，剧烈派发", False
        return SkillResult(
            name=self.name, key=self.key, score=round(score, 1),
            weight=self.weight, detail=detail, passed=passed,
        )
