"""涨停质量因子 — 权重 7%"""
from .base import BaseSkill, SkillResult
from typing import Any, Dict


class ZTQualitySkill(BaseSkill):
    name = "涨停质量"
    key = "zt_quality"
    weight = 0.07

    def score(self, ctx: Dict[str, Any]) -> SkillResult:
        ztq = ctx.get("zt_quality") or {}
        fbt = ztq.get("封板时间", 0)       # HHMMSS
        zbc = ztq.get("炸板次数", 0)
        freq = ztq.get("涨停频率", 0)

        s = 0.0
        parts = []

        # 封板时间评分
        if fbt <= 100000:       # 10:00前
            s += 5.0
            parts.append(f"封板{fbt//10000:02d}:{(fbt%10000)//100:02d}，早盘封板")
        elif fbt <= 113000:     # 11:30前
            s += 3.5
            parts.append(f"封板{fbt//10000:02d}:{(fbt%10000)//100:02d}，上午封板")
        elif fbt <= 140000:     # 14:00前
            s += 2.0
            parts.append(f"封板{fbt//10000:02d}:{(fbt%10000)//100:02d}，午后封板")
        elif fbt > 0:
            s += 0.5
            parts.append(f"封板{fbt//10000:02d}:{(fbt%10000)//100:02d}，尾盘封板")
        else:
            parts.append("无封板时间数据")

        # 炸板评分
        if zbc == 0:
            s += 3.0
            parts.append("零炸板")
        elif zbc == 1:
            s += 1.0
            parts.append(f"炸板{zbc}次")
        else:
            parts.append(f"炸板{zbc}次(>1)")

        # 涨停频率评分
        if freq == 0:
            s += 2.0
            parts.append("30天内0次涨停，非妖股")
        elif freq == 1:
            s += 1.5
            parts.append(f"30天内{freq}次涨停")
        elif freq <= 2:
            s += 0.5
            parts.append(f"30天内{freq}次涨停，略频繁")
        else:
            parts.append(f"30天内{freq}次涨停，过于频繁")

        return SkillResult(
            name=self.name, key=self.key, score=round(min(10.0, s), 1),
            weight=self.weight, detail="; ".join(parts), passed=s >= 2.0,
        )
