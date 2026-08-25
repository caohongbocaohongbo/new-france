"""邮件升级 · 可执行操作计划生成（纯规则，无外部依赖）。"""
from __future__ import annotations

from typing import Optional

from .indicators import atr, ma, recent_high, recent_low


def _round2(value: Optional[float]) -> float:
    return round(float(value), 2) if value is not None else 0.0


def build_action_plan(stock, hist) -> Optional[dict]:
    """返回操作计划 dict；数据不足返回 None（邮件端渲染为'数据不足，暂不给出操作计划'）。"""
    # 历史 K 线缺失/为空时，支撑/ATR/目标均无依据，按验收口径返回 None。
    if hist is None or not hasattr(hist, "empty") or hist.empty:
        return None
    current_price = float(getattr(stock, "current_price", 0) or 0)
    if current_price <= 0:
        return None
    score = float(getattr(stock, "adjusted_score", 0) or 0)
    ref_price = float(getattr(stock, "ref_price", 0) or 0)

    atr14 = atr(hist, 14)
    low20 = recent_low(hist, 20)
    ma10 = ma(hist, 10)
    ma20 = ma(hist, 20)
    high60 = recent_high(hist, 60)

    # 支撑位：近期低点/均线中「小于现价」的最大者，否则用现价*0.97
    support_candidates = [v for v in (low20, ma10, ma20) if v is not None and v < current_price]
    support = max(support_candidates) if support_candidates else current_price * 0.97

    buy_low = round(current_price * 0.99, 2)
    buy_high = round(current_price * 1.01, 2)
    buy_mid = current_price

    # 止损位：支撑*0.98 与 现价-1.5*ATR 的较大者；仍不低于买入下沿则退回现价*0.95
    if atr14 and atr14 > 0:
        stop_loss = max(support * 0.98, current_price - 1.5 * atr14)
    else:
        stop_loss = support * 0.98
    if stop_loss >= buy_low:
        stop_loss = current_price * 0.95
    stop_loss = round(stop_loss, 2)

    # 目标价：target_1 = 涨停参考价回补；target_2 = max(近60日高点, 参考价*1.05)
    target_1 = ref_price if ref_price > 0 else round(current_price * 1.05, 2)
    if high60 is not None:
        target_2 = max(high60, ref_price * 1.05 if ref_price > 0 else current_price * 1.10)
    else:
        target_2 = ref_price * 1.05 if ref_price > 0 else current_price * 1.10
    target_1 = round(target_1, 2)
    target_2 = round(target_2, 2)

    # 盈亏比 = (target_1 - buy_mid) / (buy_mid - stop_loss)
    spread = buy_mid - stop_loss
    risk_reward = round((target_1 - buy_mid) / spread, 2) if spread > 0 else 0.0

    # 波动率参考 = ATR / 现价
    atr_pct = round(atr14 / current_price, 4) if atr14 and atr14 > 0 else 0.0

    warnings: list = []
    if risk_reward < 1.5:
        warnings.append("盈亏比偏低(<1.5)")

    # 仓位建议
    if score >= 70 and risk_reward >= 2 and atr_pct < 0.06:
        position = "重仓(40%+)"
    elif score >= 55 and risk_reward >= 1.5:
        position = "半仓(20-30%)"
    else:
        position = "轻仓(≤10%)"

    return {
        "buy_low": buy_low,
        "buy_high": buy_high,
        "support": round(support, 2),
        "stop_loss": stop_loss,
        "target_1": target_1,
        "target_2": target_2,
        "risk_reward": risk_reward,
        "position": position,
        "atr_pct": atr_pct,
        "warnings": warnings,
    }
