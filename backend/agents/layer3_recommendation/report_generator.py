"""
报告生成器 — Markdown + HTML 报告
"""
from datetime import date
from pathlib import Path
from typing import List


def _format_seal_time(zt_quality: dict) -> str:
    """格式化封板时间"""
    fbt = zt_quality.get("封板时间", 0) if zt_quality else 0
    if not fbt or fbt == 0:
        return "--"
    hours = fbt // 10000
    mins = (fbt % 10000) // 100
    return f"{hours:02d}:{mins:02d}"


def _get_audit_info(audit_results: dict, code: str) -> dict:
    """获取某只股票的审核信息"""
    for s in audit_results.get("stocks", []):
        if s.get("code") == code:
            return s
    return {}


def _format_index_value(index_snapshot: dict) -> str:
    value = (index_snapshot or {}).get("value")
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "--"


def generate_markdown_report(stocks, target_date: date,
                              index_gain: float, output_path: Path,
                              audit_results: dict = None,
                              index_snapshot: dict = None):
    """生成每日 Markdown 报告"""
    if audit_results is None:
        audit_results = {}
    if index_snapshot is None:
        index_snapshot = {}

    date_str = target_date.strftime("%Y-%m-%d")
    lines = [
        f"# New France 尾盘涨停推荐报告",
        f"**日期**: {date_str} | **上证指数**: {_format_index_value(index_snapshot)} | **涨跌幅**: {index_gain:+.2f}%",
        "",
        "---",
        "",
    ]

    # 统计摘要
    strong = [s for s in stocks if s.recommendation == "STRONG_BUY"]
    buy = [s for s in stocks if s.recommendation == "BUY"]
    watch = [s for s in stocks if s.recommendation == "WATCH"]

    lines.extend([
        "## 统计摘要",
        "",
        f"| 等级 | 数量 |",
        f"|------|------|",
        f"| STRONG_BUY | {len(strong)} |",
        f"| BUY | {len(buy)} |",
        f"| WATCH | {len(watch)} |",
        f"| **总计** | **{len(stocks)}** |",
        "",
    ])

    # 审核概览
    if audit_results.get("stocks"):
        lines.extend([
            f"| 审核通过率 | {audit_results.get('pass_rate', 100):.0f}% |",
            f"| 审核降级 | {audit_results.get('downgraded', 0)} 只 |",
            "",
        ])

    # 推荐详情
    for level_name, level_stocks in [
        ("STRONG_BUY 强烈买入", strong),
        ("BUY 建议买入", buy),
        ("WATCH 观察", watch),
    ]:
        if not level_stocks:
            continue
        lines.append(f"## {level_name}")
        lines.append("")
        for s in level_stocks:
            audit_info = _get_audit_info(audit_results, s.code)
            # 提取因子中的 zt_quality 获取封板时间
            ztq = s.factor_scores.get("zt_quality", None)
            seal_time_str = _format_seal_time({"封板时间": s.extra.get("封板时间", 0)} if hasattr(s, "extra") and s.extra else {})

            # 基本指标
            lines.append(f"### #{s.rank} {s.name}({s.code}) — {s.adjusted_score:.0f}分")
            if audit_info.get("downgraded"):
                lines.append(f"> ⚠️ 审核降级: {audit_info['original_rec']} → {audit_info['adjusted_rec']} "
                             f"(失败{audit_info.get('fail_count',0)}项)")
            lines.append("")
            lines.append(f"| 指标 | 值 |")
            lines.append(f"|------|-----|")
            lines.append(f"| 回撤幅度 | {s.drop_pct:+.2f}% |")
            lines.append(f"| 涨停日期 | {s.zt_date} |")
            lines.append(f"| 参考价 | {s.ref_price:.2f} |")
            lines.append(f"| 现价 | {s.current_price:.2f} |")
            lines.append(f"| 数据可信度 | {audit_info.get('pass_count', '--')}/{audit_info.get('pass_count', 0) + audit_info.get('warn_count', 0) + audit_info.get('fail_count', 0)} 项通过 |")

            lines.append("")
            lines.append("**因子评分**:")
            lines.append("")
            for key, r in s.factor_scores.items():
                if key == "event_bonus":
                    continue
                mark = "+" if r.passed else "-"
                lines.append(f"- {mark} **{r.name}**({r.weight*100:.0f}%): {r.score:.1f}/10 — {r.detail}")
            lines.append("")
            lines.append("---")
            lines.append("")

    # 审核报告
    if audit_results.get("summary_md"):
        lines.append(audit_results["summary_md"])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def generate_html_report(stocks, target_date: date,
                          index_gain: float, output_path: Path,
                          audit_results: dict = None,
                          index_snapshot: dict = None) -> str:
    """生成 HTML 报告（前端展示用）"""
    if audit_results is None:
        audit_results = {}
    if index_snapshot is None:
        index_snapshot = {}
    date_str = target_date.strftime("%Y-%m-%d")
    index_value = _format_index_value(index_snapshot)

    rows_html = ""
    for s in stocks:
        stars = "\u2605" * min(4, max(1, int(s.adjusted_score / 25)))
        level_colors = {
            "STRONG_BUY": "#E74C3C", "BUY": "#F39C12",
            "WATCH": "#3498DB", "PASS": "#95A5A6",
        }
        color = level_colors.get(s.recommendation, "#95A5A6")

        factors_html = ""
        for key, r in s.factor_scores.items():
            if key == "event_bonus":
                continue
            dot = "#27AE60" if r.passed else "#555"
            factors_html += (
                f'<span class="factor-dot" style="background:{dot}" '
                f'title="{r.name}: {r.detail} ({r.score:.1f}/10)"></span>'
            )

        rows_html += f"""
        <tr>
            <td>{s.rank}</td>
            <td><a href="#" class="stock-code">{s.code}</a></td>
            <td>{s.name}</td>
            <td>{stars}</td>
            <td style="color:{color};font-weight:600">{s.adjusted_score:.0f}</td>
            <td style="color:{'#E74C3C' if s.drop_pct < 0 else '#27AE60'}">{s.drop_pct:+.2f}%</td>
            <td><span class="tag tag-{s.recommendation.lower()}">{s.recommendation.replace('_',' ')}</span></td>
            <td>{factors_html}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>New France — {date_str} 推荐报告</title>
<style>
body{{background:#0A1628;color:#E8ECF1;font-family:-apple-system,system-ui,sans-serif;margin:0;padding:24px}}
h1{{color:#D4A853;font-size:24px;margin-bottom:4px}}
.subtitle{{color:#8B95A8;font-size:14px;margin-bottom:24px}}
.stats{{display:flex;gap:16px;margin-bottom:24px}}
.stat-card{{background:#132238;border-radius:8px;padding:16px 24px;flex:1;text-align:center}}
.stat-value{{font-size:28px;font-weight:700;color:#D4A853}}
.stat-label{{font-size:12px;color:#8B95A8;margin-top:4px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{background:#1A2D4A;color:#8B95A8;padding:10px 12px;text-align:left;font-weight:500}}
td{{padding:10px 12px;border-bottom:1px solid #1A2D4A}}
tr:hover td{{background:rgba(212,168,83,0.05)}}
.stock-code{{color:#3B82F6;text-decoration:none;font-family:monospace}}
.factor-dot{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:3px}}
.tag{{padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}}
.tag-strong_buy{{background:rgba(231,76,60,0.2);color:#E74C3C}}
.tag-buy{{background:rgba(243,156,18,0.2);color:#F39C12}}
.tag-watch{{background:rgba(52,152,219,0.2);color:#3498DB}}
</style>
</head>
<body>
<h1>New France 尾盘涨停推荐报告</h1>
<p class="subtitle">{date_str} | 上证指数: {index_value} | 涨跌幅: {index_gain:+.2f}%</p>
<div class="stats">
    <div class="stat-card"><div class="stat-value">{len(stocks)}</div><div class="stat-label">总计</div></div>
    <div class="stat-card"><div class="stat-value">{sum(1 for s in stocks if s.recommendation=="STRONG_BUY")}</div><div class="stat-label">STRONG BUY</div></div>
    <div class="stat-card"><div class="stat-value">{sum(1 for s in stocks if s.recommendation=="BUY")}</div><div class="stat-label">BUY</div></div>
    <div class="stat-card"><div class="stat-value">{sum(1 for s in stocks if s.recommendation=="WATCH")}</div><div class="stat-label">WATCH</div></div>
    <div class="stat-card"><div class="stat-value">{audit_results.get('pass_rate', 100):.0f}%</div><div class="stat-label">审核通过率</div></div>
</div>
<table>
<thead><tr><th>#</th><th>代码</th><th>名称</th><th>评级</th><th>得分</th><th>回撤</th><th>推荐</th><th>因子</th></tr></thead>
<tbody>{rows_html}</tbody>
</table>
</body>
</html>"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return str(output_path)
