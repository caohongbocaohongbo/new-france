"""成交量趋势因子 — 权重 12%"""
import numpy as np
from .base import BaseSkill, SkillResult
from typing import Any, Dict


class VolumeTrendSkill(BaseSkill):
    name = "量能趋势"
    key = "volume_trend"
    weight = 0.12

    def score(self, ctx: Dict[str, Any]) -> SkillResult:
        hist = ctx.get("history")
        if hist is None or len(hist) < 6:
            return SkillResult(name=self.name, key=self.key, score=5.0,
                               weight=self.weight, detail="历史数据不足(需6日)", passed=True)

        volumes = hist['成交量'].tail(6).values[:-1]  # 前5日
        if len(volumes) < 5:
            return SkillResult(name=self.name, key=self.key, score=5.0,
                               weight=self.weight, detail="成交量数据不足", passed=True)

        x = np.arange(len(volumes)).astype(float)
        slope = np.polyfit(x, volumes, 1)[0]
        # 归一化斜率：斜率为正的幅度
        mean_vol = np.mean(volumes)
        if mean_vol > 0:
            norm_slope = slope / mean_vol
        else:
            norm_slope = 0

        if norm_slope > 0.1:
            s, detail = 10.0, f"量能稳步爬升(归一化斜率={norm_slope:.2f})，最佳形态"
        elif norm_slope > 0.03:
            s = self.linear_score(norm_slope, 0.03, 0.1, floor=5, ceil=10)
            detail = f"量能温和放大(归一化斜率={norm_slope:.2f})"
        elif norm_slope > 0:
            s = 3.0
            detail = f"量能略有放大(归一化斜率={norm_slope:.2f})，但不够显著"
        elif norm_slope > -0.05:
            s = 1.0
            detail = f"量能基本持平(归一化斜率={norm_slope:.2f})"
        else:
            s, detail = 0.0, f"量能持续萎缩(归一化斜率={norm_slope:.2f})"

        return SkillResult(
            name=self.name, key=self.key, score=round(s, 1),
            weight=self.weight, detail=detail, passed=s >= 1.0,
        )
