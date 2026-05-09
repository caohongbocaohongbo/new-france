"""强势确认因子 — 权重 10%"""
from .base import BaseSkill, SkillResult
from typing import Any, Dict


class StrengthSkill(BaseSkill):
    name = "强势确认"
    key = "strength"
    weight = 0.10

    def score(self, ctx: Dict[str, Any]) -> SkillResult:
        hist = ctx.get("history")
        if hist is None or len(hist) < 1:
            return SkillResult(name=self.name, key=self.key, score=5.0,
                               weight=self.weight, detail="无当日行情数据", passed=True)

        today = hist.iloc[-1]
        close = float(today['收盘'])
        high = float(today.get('最高', close))
        low = float(today.get('最低', close))
        day_gain = float(ctx.get("涨跌幅") or 0)
        index_gain = float(ctx.get("index_gain") or 0)

        range_pos = (close - low) / (high - low) if high > low else 0.5
        beat_market = day_gain > index_gain

        s = 0.0
        parts = []
        if range_pos > 0.60:
            s += 5.0
            parts.append(f"收盘位于区间{range_pos*100:.0f}%位(强势)")
        elif range_pos > 0.40:
            s += 2.5
            parts.append(f"收盘位于区间{range_pos*100:.0f}%位(中性)")
        else:
            parts.append(f"收盘位于区间{range_pos*100:.0f}%位(偏弱)")

        if beat_market:
            s += 5.0
            parts.append(f"涨幅{day_gain:+.2f}%强于大盘{index_gain:+.2f}%")
        elif day_gain > index_gain * 0.5:
            s += 2.5
            parts.append(f"涨幅{day_gain:+.2f}%基本持平大盘{index_gain:+.2f}%")
        else:
            parts.append(f"涨幅{day_gain:+.2f}%弱于大盘{index_gain:+.2f}%")

        return SkillResult(
            name=self.name, key=self.key, score=round(s, 1),
            weight=self.weight, detail="; ".join(parts), passed=s >= 3.0,
        )
