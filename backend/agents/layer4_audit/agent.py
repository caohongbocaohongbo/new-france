"""
Layer 4: AuditAgent — 数据审核引擎
职责: 对推荐股票执行数据真实性校验，不通过的自动降级
约束: 只读（不修改外部数据），只返回审核结果
"""
import logging
from typing import List, Dict, Any, Optional

from .validators import ALL_VALIDATORS, ValidationResult
from .audit_report import generate_audit_summary_md, generate_audit_summary_json

logger = logging.getLogger(__name__)

# 降级映射
DOWNGRADE_MAP = {
    "STRONG_BUY": "BUY",
    "BUY": "WATCH",
    "WATCH": "PASS",
    "PASS": "PASS",
}


class AuditResult:
    """单只股票的审核结果"""
    def __init__(self, code: str, name: str):
        self.code = code
        self.name = name
        self.validations: List[ValidationResult] = []
        self.fail_count = 0
        self.warn_count = 0
        self.pass_count = 0
        self.original_recommendation = ""
        self.adjusted_recommendation = ""
        self.downgraded = False

    @property
    def audit_status(self) -> str:
        if self.fail_count > 0:
            return "fail"
        if self.warn_count > 0:
            return "warn"
        return "pass"


class AuditAgent:
    """数据审核Agent — 纯校验，不修改外部状态"""

    def __init__(self, strict_mode: bool = True):
        self.strict_mode = strict_mode
        self.validators = ALL_VALIDATORS

    def audit_single(self, ctx: Dict[str, Any],
                     recommendation: str = "PASS") -> AuditResult:
        """审核单只股票"""
        code = ctx.get("code", "?")
        name = ctx.get("name", "?")
        result = AuditResult(code, name)
        result.original_recommendation = recommendation

        for validator in self.validators:
            try:
                vr = validator(ctx)
            except Exception as e:
                vr = ValidationResult("系统校验", "warn", f"校验器异常: {e}")

            result.validations.append(vr)
            if vr.status == "fail":
                result.fail_count += 1
            elif vr.status == "warn":
                result.warn_count += 1
            else:
                result.pass_count += 1

        # 降级逻辑
        if self.strict_mode and result.fail_count > 0:
            result.adjusted_recommendation = DOWNGRADE_MAP.get(
                recommendation, recommendation
            )
            # 多fail项逐级降级
            if result.fail_count >= 2:
                result.adjusted_recommendation = DOWNGRADE_MAP.get(
                    result.adjusted_recommendation, "WATCH"
                )
            result.downgraded = (
                result.adjusted_recommendation != recommendation
            )
        else:
            result.adjusted_recommendation = recommendation
            result.downgraded = False

        return result

    def audit_batch(self, stocks: list,
                    historical: Optional[Dict] = None) -> Dict[str, Any]:
        """批量审核推荐股票

        Args:
            stocks: ScoredStock 列表（含 ctx 上下文的数据）
            historical: K线历史数据dict

        Returns:
            {total_stocks, downgraded, pass_rate, stocks: [{audit per stock}], summary_md}
        """
        if not stocks:
            return {
                "total_stocks": 0, "downgraded": 0, "pass_rate": 100.0,
                "stocks": [], "summary_md": "", "summary_json": {},
            }

        per_stock = []
        downgraded_count = 0
        total_validations = 0
        passed_validations = 0

        for s in stocks:
            extra = getattr(s, "extra", {}) or {}
            quote_metrics = extra.get("quote_metrics", {}) or {}
            # 构建审核上下文
            ctx = {
                "code": s.code,
                "name": s.name,
                "current_price": s.current_price,
                "ref_price": s.ref_price,
                "drop_pct": s.drop_pct,
                "市盈率": quote_metrics.get("pe"),
                "量比": quote_metrics.get("volume_ratio"),
                "换手率": quote_metrics.get("turnover"),
                "zt_quality": {
                    "封板时间": extra.get("封板时间", 0),
                },
                "history": historical.get(s.code) if historical else None,
            }
            # 从extra字段补充zt_quality数据
            if extra:
                ctx["zt_quality"]["封板时间"] = extra.get("封板时间", 0)

            # 从factor_scores中提取实际值
            for key in ["pe", "volume_ratio", "turnover"]:
                if key in s.factor_scores:
                    pass  # 已在ctx中

            audit = self.audit_single(ctx, s.recommendation)
            if audit.downgraded:
                downgraded_count += 1
                logger.warning(
                    f"  审核降级: {s.name}({s.code}) {s.recommendation} → {audit.adjusted_recommendation}"
                )

            total_validations += len(audit.validations)
            passed_validations += audit.pass_count

            per_stock.append({
                "code": audit.code,
                "name": audit.name,
                "audit_status": audit.audit_status,
                "fail_count": audit.fail_count,
                "warn_count": audit.warn_count,
                "pass_count": audit.pass_count,
                "original_rec": audit.original_recommendation,
                "adjusted_rec": audit.adjusted_recommendation,
                "downgraded": audit.downgraded,
                "validations": [
                    {"name": v.name, "status": v.status, "detail": v.detail}
                    for v in audit.validations
                ],
            })

        pass_rate = (passed_validations / total_validations * 100) if total_validations > 0 else 100.0

        result = {
            "total_stocks": len(stocks),
            "downgraded": downgraded_count,
            "pass_rate": round(pass_rate, 1),
            "stocks": per_stock,
        }
        result["summary_md"] = generate_audit_summary_md(result)
        result["summary_json"] = generate_audit_summary_json(result)

        logger.info(
            f"AuditAgent: {len(stocks)}只审核完成, "
            f"通过率{pass_rate:.0f}%, 降级{downgraded_count}只"
        )
        return result
