"""尾盘买点因子 — 权重 10%"""
from .base import BaseSkill, SkillResult
from typing import Any, Dict


class EntryPointSkill(BaseSkill):
    name = "尾盘买点"
    key = "entry_point"
    weight = 0.10

    def score(self, ctx: Dict[str, Any]) -> SkillResult:
        hist = ctx.get("history")
        if hist is None or len(hist) < 1:
            return SkillResult(name=self.name, key=self.key, score=5.0,
                               weight=self.weight, detail="无当日行情数据", passed=True)

        today = hist.iloc[-1]
        high = float(today['最高'])
        close = float(today['收盘'])
        day_gain = float(ctx.get("涨跌幅") or 0)

        if high <= 0:
            return SkillResult(name=self.name, key=self.key, score=0,
                               weight=self.weight, detail="数据异常", passed=False)

        ratio = close / high
        if ratio >= 0.97 and day_gain > 0:
            s, detail = 10.0, f"收盘/最高={ratio*100:.1f}%，尾盘强势收高，绝佳买点"
        elif ratio >= 0.97:
            s, detail = 6.0, f"收盘/最高={ratio*100:.1f}%，高位但涨幅为负"
        elif ratio >= 0.95:
            s, detail = 6.0, f"收盘/最高={ratio*100:.1f}%，尾盘形态尚可"
        elif ratio >= 0.93:
            s, detail = 3.0, f"收盘/最高={ratio*100:.1f}%，尾盘有所回落"
        elif ratio >= 0.90:
            s, detail = 1.0, f"收盘/最高={ratio*100:.1f}%，尾盘回落明显"
        else:
            s, detail = 0.0, f"收盘/最高={ratio*100:.1f}%，尾盘走势弱"

        return SkillResult(
            name=self.name, key=self.key, score=round(s, 1),
            weight=self.weight, detail=detail, passed=s >= 1.0,
        )
