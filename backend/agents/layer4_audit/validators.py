"""数据校验器集合 — 每个校验器独立、可组合"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ValidationResult:
    """单条校验结果"""
    name: str           # 校验项名称
    status: str         # "pass" / "warn" / "fail"
    detail: str         # 详细说明
    weight: float = 1.0  # 严重程度权重


def validate_price_reasonable(ctx: Dict[str, Any]) -> ValidationResult:
    """价格合理性校验：现价在1-10000区间，与参考价同数量级"""
    current = ctx.get("current_price", 0)
    ref = ctx.get("ref_price", 0)

    if current <= 0:
        return ValidationResult("价格校验", "fail", "现价无效或缺失", weight=1.5)

    if current < 1 or current > 10000:
        return ValidationResult("价格校验", "fail",
                                f"现价{current:.2f}超出A股合理区间(1-10000)", weight=1.5)

    if ref > 0:
        ratio = abs(current - ref) / ref
        if ratio > 0.5:
            return ValidationResult("价格校验", "warn",
                                    f"现价{current:.2f}与参考价{ref:.2f}偏差{ratio*100:.0f}%，波动较大")
        if ratio > 0.9:
            return ValidationResult("价格校验", "fail",
                                    f"现价{current:.2f}与参考价{ref:.2f}偏差{ratio*100:.0f}%，疑似数据异常",
                                    weight=1.5)

    return ValidationResult("价格校验", "pass", f"现价{current:.2f}在合理范围")


def validate_seal_time(ctx: Dict[str, Any]) -> ValidationResult:
    """封板时间有效性校验"""
    ztq = ctx.get("zt_quality", {})
    fbt = ztq.get("封板时间", 0) if isinstance(ztq, dict) else 0

    if fbt == 0:
        return ValidationResult("封板时间", "fail",
                                "封板时间为0，数据缺失，涨停质量因子不可信", weight=1.0)

    # 封板时间格式: HHMMSS 或 HHMMSSmmm
    hours = fbt // 10000
    mins = (fbt % 10000) // 100
    if hours < 9 or hours > 15 or mins < 0 or mins > 59:
        return ValidationResult("封板时间", "fail",
                                f"封板时间{fbt}格式异常({hours:02d}:{mins:02d})", weight=1.0)

    if hours > 14:
        return ValidationResult("封板时间", "warn",
                                f"尾盘封板({hours:02d}:{mins:02d})，涨停质量较低")

    return ValidationResult("封板时间", "pass",
                            f"封板时间{hours:02d}:{mins:02d}有效")


def validate_data_completeness(ctx: Dict[str, Any]) -> ValidationResult:
    """数据完整性校验：K线天数是否满足因子计算最低要求"""
    hist = ctx.get("history")
    hist_days = len(hist) if hist is not None else 0

    if hist_days == 0:
        return ValidationResult("数据完整性", "fail",
                                "K线数据完全缺失，均线/量能/回撤因子均不可信", weight=2.0)

    if hist_days < 6:
        return ValidationResult("数据完整性", "fail",
                                f"K线仅{hist_days}日(需≥6日)，量能趋势因子不可信", weight=1.5)

    if hist_days < 20:
        return ValidationResult("数据完整性", "warn",
                                f"K线仅{hist_days}日(需≥20日)，均线多头因子不可信")

    return ValidationResult("数据完整性", "pass", f"K线{hist_days}日，满足全部因子计算需求")


def validate_drop_consistency(ctx: Dict[str, Any]) -> ValidationResult:
    """回撤一致性校验：累计回撤与候选中的drop_pct比对"""
    current = ctx.get("current_price", 0)
    ref = ctx.get("ref_price", 0)
    reported_drop = ctx.get("drop_pct", 0)
    price_history = ctx.get("price_history") or []

    if ref <= 0 or current <= 0:
        return ValidationResult("回撤校验", "fail", "无法计算回撤，价格数据缺失", weight=1.0)

    computed_drop = None
    for row in reversed(price_history):
        try:
            value = float(row.get("drawdown_pct"))
        except (AttributeError, TypeError, ValueError):
            continue
        if value == value:
            computed_drop = value
            break
    if computed_drop is None:
        computed_drop = (current - ref) / ref * 100
    diff = abs(computed_drop - reported_drop)

    if diff > 1.0:
        return ValidationResult("回撤校验", "fail",
                                f"累计回撤{computed_drop:+.2f}%与上报{reported_drop:+.2f}%偏差{diff:.2f}%",
                                weight=1.5)
    if diff > 0.5:
        return ValidationResult("回撤校验", "warn",
                                f"累计回撤{computed_drop:+.2f}%与上报{reported_drop:+.2f}%有轻微偏差{diff:.2f}%")

    return ValidationResult("回撤校验", "pass",
                            f"累计回撤{computed_drop:+.2f}%验证一致")


def validate_financial_metrics(ctx: Dict[str, Any]) -> ValidationResult:
    """财务指标合理性校验：PE/量比/换手率是否在合理区间"""
    pe = ctx.get("市盈率")
    vr = ctx.get("量比")
    to = ctx.get("换手率")

    issues = []
    warnings = []

    if pe is not None and pe > 0:
        if pe < 0:
            issues.append(f"PE={pe}为负值(亏损)")
        elif pe > 1000:
            issues.append(f"PE={pe}异常偏高")
    else:
        warnings.append("PE数据缺失")

    if vr is not None and vr > 0:
        if vr > 100:
            issues.append(f"量比={vr}异常偏高")
    else:
        warnings.append("量比数据缺失")

    if to is not None and to > 0:
        if to > 50:
            issues.append(f"换手率={to}%异常偏高")
    else:
        warnings.append("换手率数据缺失")

    if issues:
        return ValidationResult("财务校验", "fail",
                                "; ".join(issues), weight=1.0)
    if warnings:
        return ValidationResult("财务校验", "warn",
                                "; ".join(warnings))

    pe_str = f"PE={pe:.1f}" if (pe is not None and pe > 0) else "PE=缺失"
    return ValidationResult("财务校验", "pass",
                            f"{pe_str}, 量比={vr or '缺失'}, 换手率={to or '缺失'}%")


# 所有校验器（按执行顺序）
ALL_VALIDATORS = [
    validate_price_reasonable,
    validate_seal_time,
    validate_data_completeness,
    validate_drop_consistency,
    validate_financial_metrics,
]
