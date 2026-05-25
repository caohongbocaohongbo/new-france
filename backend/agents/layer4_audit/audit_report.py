"""审核报告生成器"""
from typing import List
from .validators import ValidationResult


def format_seal_time(fbt: int) -> str:
    """将封板时间整数格式化为 HH:MM"""
    if not fbt or fbt == 0:
        return "--"
    hours = fbt // 10000
    mins = (fbt % 10000) // 100
    return f"{hours:02d}:{mins:02d}"


def generate_audit_summary_md(audit_results: dict) -> str:
    """生成审核摘要 Markdown，追加到每日报告末尾"""
    if not audit_results:
        return ""

    total_stocks = audit_results.get("total_stocks", 0)
    downgraded = audit_results.get("downgraded", 0)
    pass_rate = audit_results.get("pass_rate", 100.0)
    per_stock = audit_results.get("stocks", [])

    lines = [
        "",
        "---",
        "",
        "## 数据审核报告",
        "",
        f"审核股票: {total_stocks} 只 | 通过率: {pass_rate:.0f}% | 降级: {downgraded} 只",
        "",
    ]

    if per_stock:
        for s in per_stock:
            code = s.get("code", "")
            name = s.get("name", "")
            status = s.get("audit_status", "pass")
            status_icon = {"pass": "✅", "warn": "⚠️", "fail": "❌"}.get(status, "?")
            lines.append(f"### {status_icon} {name}({code})")
            lines.append("")
            for v in s.get("validations", []):
                v_status = v.get("status", "pass")
                v_icon = {"pass": "✓", "warn": "⚠", "fail": "✗"}.get(v_status, "?")
                lines.append(f"- {v_icon} **{v.get('name')}**: {v.get('detail')}")
            lines.append("")

    return "\n".join(lines)


def generate_audit_summary_json(audit_results: dict) -> dict:
    """生成审核摘要 JSON（API 返回用）"""
    issues = []
    for s in audit_results.get("stocks", []):
        for v in s.get("validations", []):
            if v.get("status") in ("warn", "fail"):
                issues.append({
                    "code": s.get("code"),
                    "name": s.get("name"),
                    "field": v.get("name"),
                    "issue": v.get("detail"),
                    "severity": v.get("status"),
                })

    return {
        "total_entries": audit_results.get("total_stocks", 0),
        "issues": issues,
        "pass_rate": audit_results.get("pass_rate", 100.0),
    }
