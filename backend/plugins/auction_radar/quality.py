"""28 低位竞价雷达 QuoteQualityGuard：阶段/单位/新鲜度/跨源校验。"""
from __future__ import annotations

from datetime import datetime

from .config import BEIJING_TZ
from .models import AuctionQuote, QualityResult


def _now():
    return datetime.now(BEIJING_TZ)


def validate_quote(quote: AuctionQuote, trade_day: str, phase: str,
                   previous_quote: AuctionQuote = None) -> QualityResult:
    """返回 quality/reasons/allowed_fields/lag_ms/fallback_required。"""
    reasons = []
    allowed = []
    fallback = False

    code = (quote.code or "").strip().zfill(6)
    if len(code) != 6 or not code.isdigit():
        return QualityResult("INVALID", ["code 非法"], [], None, False)
    if quote.trade_date and str(quote.trade_date) != str(trade_day):
        reasons.append("交易日不匹配")
    if quote.indicative_price_yuan is not None and quote.indicative_price_yuan <= 0:
        reasons.append("虚拟参考价非正")
        quote.indicative_price_yuan = None
    if quote.prev_close_yuan is not None and quote.prev_close_yuan <= 0:
        reasons.append("昨收非正")
        quote.prev_close_yuan = None
    if quote.indicative_price_yuan is not None:
        allowed.append("indicative_price_yuan")
    if quote.prev_close_yuan is not None:
        allowed.append("prev_close_yuan")

    # 价格限制（软校验；仅当涨跌停价有效时）
    if quote.price_limit_up_yuan and quote.indicative_price_yuan:
        if quote.indicative_price_yuan > quote.price_limit_up_yuan * 1.005:
            reasons.append("超涨停价")
    if quote.price_limit_down_yuan and quote.indicative_price_yuan:
        if quote.indicative_price_yuan < quote.price_limit_down_yuan * 0.995:
            reasons.append("破跌停价")

    # 时间戳不倒退
    if previous_quote is not None and previous_quote.received_at and quote.received_at:
        try:
            prev = datetime.fromisoformat(previous_quote.received_at)
            cur = datetime.fromisoformat(quote.received_at)
            if cur < prev:
                reasons.append("时间戳倒退")
        except ValueError:
            pass

    # 来源不一致降级
    if previous_quote is not None and previous_quote.source and quote.source and             previous_quote.source != quote.source:
        reasons.append("跨源切换")
        fallback = True

    # 虚拟参考价未验证（能力表未通过）不得正式入分
    if quote.field_capability_version in ("unverified", ""):
        reasons.append("字段能力未验证")
        # 仍允许用于取证/观察，但不得正式评分
        fallback = True

    if "交易日不匹配" in "".join(reasons) or len(code) != 6:
        return QualityResult("INVALID", reasons, allowed, None, fallback)

    if reasons and any("字段能力未验证" not in r and "跨源切换" not in r for r in reasons):
        return QualityResult("PARTIAL", reasons, allowed, None, fallback)

    if fallback and not reasons:
        return QualityResult("VALID", ["能力未验证，仅观察"], allowed, None, True)

    return QualityResult("VALID", reasons, allowed, None, fallback)
