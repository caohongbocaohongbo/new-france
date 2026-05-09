"""事件驱动加分因子 — 额外加减分，不计入权重分母"""
from .base import BaseSkill, SkillResult
from typing import Any, Dict, List


class EventBonusSkill(BaseSkill):
    name = "事件驱动加分"
    key = "event_bonus"
    weight = 0.00   # 额外加减分

    def score(self, ctx: Dict[str, Any]) -> SkillResult:
        events: List[Dict] = ctx.get("events") or []
        max_bonus = ctx.get("event_params", {}).get("max_bonus", 8.0)
        max_penalty = ctx.get("event_params", {}).get("max_penalty", -8.0)

        if not events:
            return SkillResult(name=self.name, key=self.key, score=0.0,
                               weight=self.weight, detail="无关联事件", passed=True)

        s = 0.0
        parts = []
        for evt in events:
            impact = float(evt.get("impact_score", 0))
            title = evt.get("title", "未知事件")
            etype = evt.get("event_type", "")
            if impact > 0:
                parts.append(f"+{impact:.0f}:{title}")
            else:
                parts.append(f"{impact:.0f}:{title}")
            s += impact

        s = max(max_penalty, min(max_bonus, s))
        detail = "事件驱动: " + "; ".join(parts) if parts else "无关联事件"

        return SkillResult(
            name=self.name, key=self.key, score=round(s, 1),
            weight=self.weight, detail=detail, passed=True,
        )
