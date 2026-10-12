"""风险否决/降级规则层（26 方案功能 1）。

在 11 因子线性加权评分 + Layer2.5 数据审核之后、推荐等级最终确定之前，
对每个候选再施加一层可解释的风险否决/降级规则。

设计约束：
- 纯函数、无 IO、可单测；
- 只降级不升级（保守）；
- 命中规则写入 override_hits / override_reasons，便于回测拦截率。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# 等级顺序：从高到低。降级沿列表向后移动。
LEVEL_ORDER = ["STRONG_BUY", "BUY", "WATCH", "HOLD"]

# 默认阈值（主板口径，全部可通过 config 覆盖）
DEFAULT_CONFIG: Dict[str, float] = {
    "enabled": 1,
    "max_5d_gain": 15.0,        # R1 追高：5 日累计涨幅上限(%)
    "rsi_overbought": 70.0,     # R2 超买：RSI14 阈值
    "near_high_pct": 90.0,      # R2 近高：现价处于 20 日高位的分位(%)
    "max_break_count": 8,       # R3 炸板：炸板次数上限
    "min_float_cap_yi": 20.0,   # R4 流动性：流通市值下限(亿)
    "max_turnover": 25.0,       # R4 异常换手：换手率上限(%)
    "risk_off_index_drop": -2.0,  # R5 大盘 risk-off：当日跌幅阈值(%)
    "days_before_earnings": 5,  # R6 财报前：距披露交易日数
}


@dataclass
class OverrideResult:
    level: str
    hits: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    confidence_multiplier: float = 1.0


def downgrade(level: str, steps: int = 1) -> str:
    """沿 LEVEL_ORDER 向下移动 steps 档，最低到 HOLD。"""
    try:
        idx = LEVEL_ORDER.index(level)
    except ValueError:
        idx = len(LEVEL_ORDER) - 1
    return LEVEL_ORDER[min(idx + steps, len(LEVEL_ORDER) - 1)]


def apply_risk_overrides(
    ctx: Dict[str, Any],
    level: str,
    config: Optional[Dict[str, float]] = None,
) -> OverrideResult:
    """对单个股票施加 7 条风险规则，返回最终等级 + 命中记录。

    ctx 字段（全部可缺失，缺省视为无风险）：
        pct_change_5d, rsi14, near_high_20d(bool),
        break_count, float_market_cap_yi, turnover_rate,
        index_gain, index_drop_3d(bool),
        days_to_earnings, has_crisis_news(bool)
    """
    cfg = dict(DEFAULT_CONFIG)
    if config:
        cfg.update({k: v for k, v in config.items() if v is not None})
    if not cfg.get("enabled", 1):
        return OverrideResult(level=level)

    result = OverrideResult(level=level)
    if level not in LEVEL_ORDER:
        return result

    # R7 黑天鹅/舆情：最强否决，直接压到 WATCH
    if ctx.get("has_crisis_news"):
        result.hits.append("R7")
        result.reasons.append("命中危机/黑天鹅舆情关键词")
        result.level = min_rank(result.level, "WATCH")
        return result

    # R1 追高否决：5 日涨幅过大
    g5 = _num(ctx.get("pct_change_5d"))
    if g5 is not None and g5 > cfg["max_5d_gain"]:
        result.hits.append("R1")
        result.reasons.append(f"5日涨幅{g5:.1f}%>{cfg['max_5d_gain']:.0f}%")
        result.level = downgrade(result.level)
        result.confidence_multiplier *= 0.7

    # R2 超买否决：RSI 超买 + 近 20 日高位
    rsi = _num(ctx.get("rsi14"))
    if rsi is not None and rsi > cfg["rsi_overbought"] and ctx.get("near_high_20d"):
        result.hits.append("R2")
        result.reasons.append(f"RSI{rsi:.0f}>超买且近20日高位")
        result.level = downgrade(result.level)

    # R3 炸板风险
    bc = _num(ctx.get("break_count"))
    if bc is not None and bc > cfg["max_break_count"]:
        result.hits.append("R3")
        result.reasons.append(f"炸板{int(bc)}次>{int(cfg['max_break_count'])}次")
        result.level = downgrade(result.level)

    # R4 流动性/异常换手：封顶 WATCH
    cap = _num(ctx.get("float_market_cap_yi"))
    tr = _num(ctx.get("turnover_rate"))
    if (cap is not None and cap < cfg["min_float_cap_yi"]) or (tr is not None and tr > cfg["max_turnover"]):
        result.hits.append("R4")
        result.reasons.append(
            f"流通市值{cap if cap is not None else '-'}亿/换手{tr if tr is not None else '-'}%异常"
        )
        result.level = min_rank(result.level, "WATCH")

    # R5 大盘 risk-off：不降级，降置信
    ig = _num(ctx.get("index_gain"))
    if (ig is not None and ig < cfg["risk_off_index_drop"]) or ctx.get("index_drop_3d"):
        result.hits.append("R5")
        result.reasons.append("大盘 risk-off，置信度下调")
        result.confidence_multiplier *= 0.7

    # R6 财报前风险
    de = _num(ctx.get("days_to_earnings"))
    if de is not None and 0 <= de < cfg["days_before_earnings"]:
        result.hits.append("R6")
        result.reasons.append(f"距财报披露{int(de)}个交易日")
        result.level = downgrade(result.level)

    return result


def min_rank(level: str, floor: str) -> str:
    """返回 level 与 floor 中较低（更靠后）的一档。"""
    try:
        return level if LEVEL_ORDER.index(level) >= LEVEL_ORDER.index(floor) else floor
    except ValueError:
        return floor


def _num(value: Any) -> Optional[float]:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n else None  # NaN 视为缺失


def build_stock_ctx(
    code: str,
    level: str,
    price_history: Optional[List[Dict[str, Any]]] = None,
    historical_df=None,
    break_count: Optional[int] = None,
    float_market_cap_yi: Optional[float] = None,
    turnover_rate: Optional[float] = None,
    index_gain: Optional[float] = None,
    index_drop_3d: bool = False,
    days_to_earnings: Optional[int] = None,
    has_crisis_news: bool = False,
) -> Dict[str, Any]:
    """从 screening 上下文中构建 risk_overrides 所需 ctx。

    price_history: 每个元素含 close（可选），用于算 5 日涨幅。
    historical_df: 该股历史 K 线 DataFrame（含收盘价列），用于算 RSI/近高/5日涨幅。
    """
    ctx: Dict[str, Any] = {
        "code": code,
        "break_count": break_count,
        "float_market_cap_yi": float_market_cap_yi,
        "turnover_rate": turnover_rate,
        "index_gain": index_gain,
        "index_drop_3d": index_drop_3d,
        "days_to_earnings": days_to_earnings,
        "has_crisis_news": has_crisis_news,
    }
    closes = _extract_closes(price_history) or _extract_closes_from_df(historical_df)
    if closes:
        ctx["pct_change_5d"] = _pct_change_n(closes, 5)
        ctx["rsi14"] = _rsi(closes, 14)
        ctx["near_high_20d"] = _near_high(closes, 20, 90.0)
    return ctx


def _extract_closes(price_history: Optional[List[Dict[str, Any]]]) -> Optional[List[float]]:
    if not price_history:
        return None
    out = []
    for row in price_history:
        try:
            c = float(row.get("close") if isinstance(row, dict) else None)
            if c == c:
                out.append(c)
        except (TypeError, ValueError):
            continue
    return out or None


def _extract_closes_from_df(df) -> Optional[List[float]]:
    if df is None or getattr(df, "empty", False):
        return None
    for col in ("close", "收盘", "收盘价"):
        if col in df.columns:
            vals = [float(v) for v in df[col].tolist() if v == v]
            return vals or None
    return None


def _pct_change_n(closes: List[float], n: int) -> Optional[float]:
    if len(closes) < n + 1:
        return None
    base = closes[-n - 1]
    if base == 0:
        return None
    return (closes[-1] - base) / base * 100


def _rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _near_high(closes: List[float], window: int = 20, pct: float = 90.0) -> bool:
    if len(closes) < window:
        return False
    recent = closes[-window:]
    hi = max(recent)
    if hi == 0:
        return False
    pos = (closes[-1] - min(recent)) / (hi - min(recent)) * 100 if hi != min(recent) else 100.0
    return pos >= pct
