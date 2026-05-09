"""均线多头因子 — 权重 12%"""
import numpy as np
from .base import BaseSkill, SkillResult
from typing import Any, Dict


class MAAlignmentSkill(BaseSkill):
    name = "均线多头"
    key = "ma_alignment"
    weight = 0.12

    def score(self, ctx: Dict[str, Any]) -> SkillResult:
        hist = ctx.get("history")
        if hist is None or len(hist) < 20:
            return SkillResult(name=self.name, key=self.key, score=5.0,
                               weight=self.weight, detail="历史数据不足(需20日)", passed=True)

        close = hist['收盘'].values
        ma5 = float(np.mean(close[-5:]))
        ma10 = float(np.mean(close[-10:]))
        ma20 = float(np.mean(close[-20:]))
        has_ma60 = len(close) >= 60
        ma60 = float(np.mean(close[-60:])) if has_ma60 else None

        lines = 0
        details = []
        if ma5 > ma10:
            lines += 1
            details.append(f"MA5({ma5:.2f})>MA10({ma10:.2f})")
        if ma10 > ma20:
            lines += 1
            details.append(f"MA10({ma10:.2f})>MA20({ma20:.2f})")
        if ma60 is not None and ma20 > ma60:
            lines += 1
            details.append(f"MA20({ma20:.2f})>MA60({ma60:.2f})")

        detail_str = "多头排列:" + ", ".join(details) if details else "非多头排列"

        if lines == 3:
            s = 10.0
        elif lines == 2:
            s = 7.0
        elif lines == 1:
            s = 4.0
        else:
            s = 0.0

        return SkillResult(
            name=self.name, key=self.key, score=round(s, 1),
            weight=self.weight, detail=detail_str, passed=s >= 4.0,
        )
