"""
通知模块 — 邮件通知（Brevo HTTP API 优先，SMTP 备选）
HTML 邮件格式，涨停股列表使用表头底色 + 斑马纹表格
"""
import os
import json
import smtplib
import logging
import html
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date
from typing import List, Optional

logger = logging.getLogger(__name__)

NOTIFY_CONFIG = {
    "email_enabled": True,
    "email_user": os.environ.get("SMTP_USER", ""),
    "email_to": os.environ.get("SMTP_TO", os.environ.get("SMTP_USER", "")),
}
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")

WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 涨停列表排序列名映射
ZT_SORT_FIELDS = {
    "change_pct": "change_pct",
    "turnover": "turnover",
    "vol_ratio": "vol_ratio",
    "pe": "pe",
    "mcap": "mcap",
    "seal_time": "seal_time",
}
ZT_SORT_LABELS = {
    "change_pct": "涨幅", "turnover": "换手", "vol_ratio": "量比",
    "pe": "PE", "mcap": "流通市值", "seal_time": "封板",
}


def _get_zt_sort_config():
    """读取涨停列表排序配置，默认按封板时间升序"""
    try:
        from ...services.runtime_config import get_effective_config
        runtime_sort = get_effective_config()["config"].get("ztSort", {})
        sort_by = runtime_sort.get("sortBy", "seal_time")
        sort_order = runtime_sort.get("sortOrder", "asc")
        if sort_by in ZT_SORT_FIELDS and sort_order in ("asc", "desc"):
            return sort_by, sort_order
    except Exception:
        pass

    try:
        from config.strategy_params import ZT_SORT_CONFIG
        sort_by = ZT_SORT_CONFIG.get("sort_by", "seal_time")
        sort_order = ZT_SORT_CONFIG.get("sort_order", "asc")
        if sort_by not in ZT_SORT_FIELDS:
            sort_by = "seal_time"
        if sort_order not in ("asc", "desc"):
            sort_order = "asc"
        return sort_by, sort_order
    except Exception:
        return "seal_time", "asc"


def _get_notify_config() -> dict:
    """读取当前邮件配置；收件人/发件人优先使用后端配置表。"""
    config = {
        "email_enabled": NOTIFY_CONFIG.get("email_enabled", True),
        "email_host": os.environ.get("SMTP_HOST", "smtp.qq.com"),
        "email_port": int(os.environ.get("SMTP_PORT", "465")),
        "email_user": NOTIFY_CONFIG.get("email_user") or os.environ.get("SMTP_USER", ""),
        "email_to": NOTIFY_CONFIG.get("email_to") or os.environ.get("SMTP_TO", os.environ.get("SMTP_USER", "")),
    }
    try:
        from ...services.runtime_config import get_effective_config
        runtime = get_effective_config()["config"].get("notification", {})
        config.update({
            "email_enabled": bool(runtime.get("emailEnabled", config["email_enabled"])),
            "email_host": runtime.get("emailHost") or config["email_host"],
            "email_port": int(runtime.get("emailPort") or config["email_port"]),
            "email_user": runtime.get("emailUser") or config["email_user"],
            "email_to": runtime.get("emailTo") or config["email_to"],
        })
    except Exception:
        pass
    return config


def _sort_zt_list(zt_list: list, sort_by: str, sort_order: str) -> list:
    """对涨停列表排序（None 值排到最后）"""
    if not zt_list or sort_by not in ZT_SORT_FIELDS:
        return zt_list

    key_fn = {
        "change_pct": lambda z: (z.get("change_pct") is None, z.get("change_pct") or 0),
        "turnover": lambda z: (z.get("turnover") is None, z.get("turnover") or 0),
        "vol_ratio": lambda z: (z.get("vol_ratio") is None, z.get("vol_ratio") or 0),
        "pe": lambda z: (z.get("pe") is None, z.get("pe") or 0),
        "mcap": lambda z: (z.get("mcap") is None, z.get("mcap") or 0),
        "seal_time": lambda z: (z.get("seal_time") is None or z.get("seal_time") == 0,
                                z.get("seal_time") or 999999),
    }[sort_by]

    reverse = sort_order == "desc"
    return sorted(zt_list, key=key_fn, reverse=reverse)


def _format_seal_time(value) -> str:
    if not value:
        return "--"
    try:
        num = int(float(value))
    except (TypeError, ValueError):
        return "--"
    if num <= 0:
        return "--"
    text = str(num).zfill(6)
    if len(text) > 6:
        text = text[:6]
    return f"{text[:2]}:{text[2:4]}"


def _fmt_num(value, digits=1, suffix=""):
    if value is None or value == "":
        return "--"
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "--"


def _build_price_history_series(hist, watch_date: str, ref_price: float) -> list:
    """邮件渲染用的轻量序列生成，便于独立测试。"""
    from ...services.drawdown import build_cumulative_price_history_series
    return build_cumulative_price_history_series(hist, watch_date, ref_price, limit=12)


def _stock_price_history(stock) -> list:
    extra = getattr(stock, "extra", {}) or {}
    rows = extra.get("price_history") or []
    if not rows:
        return []
    normalized = []
    for row in rows[-12:]:
        try:
            normalized.append({
                "date": str(row.get("date", ""))[:10],
                "close": float(row.get("close", 0)),
                "change_pct": float(row.get("change_pct", 0)),
                "drawdown_pct": float(row.get("drawdown_pct", 0)),
            })
        except (TypeError, ValueError):
            continue
    return normalized


def _audit_info_for(audit_results, code: str) -> dict:
    for item in (audit_results.get("stocks", []) if audit_results else []):
        if item.get("code") == code:
            return item
    return {}


def _failed_audit_items(audit_info: dict) -> list:
    return [
        item for item in audit_info.get("validations", [])
        if item.get("status") == "fail"
    ]


def _get_watchlist_count() -> int:
    """获取监控列表真实条数（与前端 /watchlist/stats 一致，已去重）"""
    try:
        from pathlib import Path
        import re
        france_file = Path(__file__).resolve().parent.parent.parent.parent / "data" / "france.md"
        if not france_file.exists():
            return 0
        content = france_file.read_text(encoding="utf-8")
        entries = []
        for line in content.splitlines():
            m = re.match(r"\|\s*(\d{6})\s*\|\s*(.+?)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([\d.]+)\s*\|", line)
            if m:
                entries.append(m.group(1))
        return len(set(entries))  # 去重后条数
    except Exception:
        return 0


def _format_index_value(index_snapshot: dict) -> str:
    value = (index_snapshot or {}).get("value")
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "--"


def _format_index_source(index_snapshot: dict) -> str:
    index_snapshot = index_snapshot or {}
    source = index_snapshot.get("source") or "--"
    fetched_at = index_snapshot.get("fetched_at") or "--"
    return f"数据源={source} 采集={fetched_at}"


def _national_change_counts(entity: dict) -> str:
    counts = entity.get("change_counts") or {}
    return (
        f"首次记录{counts.get('new', 0)} | "
        f"增持{counts.get('increase', 0)} | "
        f"减持{counts.get('decrease', 0)} | "
        f"退出{counts.get('exit', 0)}"
    )


def _text_national_team_section(national_team: dict) -> list:
    if not national_team:
        return []
    source_status = national_team.get("source_status") or {}
    lines = [
        "【国家队动向】",
        f"数据源: {source_status.get('source') or '--'} | 采集: {source_status.get('fetched_at') or '--'}",
    ]
    if national_team.get("scope_note"):
        lines.append(f"口径: {national_team.get('scope_note')}")
    if not source_status.get("ok", False):
        lines.append(f"数据源暂不可用: {source_status.get('error') or source_status.get('note') or '本次不生成国家队判断'}")
        return lines

    entities = national_team.get("entities") or []
    if not entities:
        lines.append("未发现新的公开披露变化。")
    for entity in entities:
        lines.append(
            f"  {entity.get('entity_name')}: 持仓{entity.get('holding_count', 0)}条 | "
            f"报告期:{entity.get('latest_report_period') or '--'} | "
            f"公告日:{entity.get('latest_notice_date') or '--'} | "
            f"{_national_change_counts(entity)}"
        )
        latest_event = entity.get("latest_event")
        if latest_event:
            lines.append(f"    最新事件: {latest_event.get('event_date') or '--'} {latest_event.get('title') or '--'}")

    events = national_team.get("events") or []
    if events:
        lines.append("  最新可查事件:")
        for event in events[:5]:
            lines.append(
                f"    {event.get('event_date') or '--'} "
                f"{event.get('entity_name') or ''} {event.get('title') or ''} "
                f"来源:{event.get('source') or '--'}"
            )
    else:
        lines.append("  未发现新的可查事件。")
    lines.append("  说明: 十大股东/十大流通股东为公开披露数据，系统每日检查公开源更新，不推断未披露交易。")
    return lines


def _html_national_team_section(national_team: dict) -> str:
    if not national_team:
        return ""
    source_status = national_team.get("source_status") or {}
    source_name = html.escape(str(source_status.get("source") or "--"))
    fetched_at = html.escape(str(source_status.get("fetched_at") or "--"))
    source_url = html.escape(str(source_status.get("source_url") or "#"))

    if not source_status.get("ok", False):
        note = html.escape(str(source_status.get("error") or source_status.get("note") or "本次不生成国家队判断"))
        return f"""
<div style="background:#FFFFFF;border:1px solid #E1E7EF;border-radius:10px;padding:20px 24px;margin-bottom:20px">
  <h2 style="color:#172033;font-size:16px;margin:0 0 10px 0;font-weight:600">国家队动向</h2>
  <p style="color:#E74C3C;font-size:13px;margin:0">数据源暂不可用：{note}</p>
  <p style="color:#667085;font-size:11px;margin:8px 0 0 0">数据源:{source_name} | 采集:{fetched_at}</p>
</div>"""

    cards = ""
    for entity in national_team.get("entities") or []:
        counts = entity.get("change_counts") or {}
        latest_event = entity.get("latest_event") or {}
        cards += f"""
      <td style="padding:12px;background:#F8FAFC;border:1px solid #E1E7EF;border-radius:8px;vertical-align:top">
        <div style="color:#172033;font-size:14px;font-weight:700;margin-bottom:6px">{html.escape(str(entity.get('entity_name') or '--'))}</div>
        <div style="color:#667085;font-size:11px;line-height:1.7">
          最近命中 {entity.get('holding_count', 0)} 条<br>
          报告期 {html.escape(str(entity.get('latest_report_period') or '--'))}<br>
          公告日 {html.escape(str(entity.get('latest_notice_date') or '--'))}<br>
          首次记录 {counts.get('new', 0)} / 增持 {counts.get('increase', 0)} / 减持 {counts.get('decrease', 0)} / 退出 {counts.get('exit', 0)}
        </div>
        <div style="color:#172033;font-size:11px;margin-top:8px;line-height:1.5">{html.escape(str(latest_event.get('title') or '未发现新的可查事件'))}</div>
      </td>"""

    events_html = ""
    for event in (national_team.get("events") or [])[:5]:
        events_html += f"""
    <tr>
      <td style="padding:7px 8px;color:#5B6472;font-size:11px">{html.escape(str(event.get('event_date') or '--'))}</td>
      <td style="padding:7px 8px;color:#172033;font-size:12px;font-weight:600">{html.escape(str(event.get('entity_name') or '--'))}</td>
      <td style="padding:7px 8px;color:#172033;font-size:12px">{html.escape(str(event.get('title') or '--'))}</td>
      <td style="padding:7px 8px;color:#667085;font-size:11px">{html.escape(str(event.get('source') or '--'))}</td>
    </tr>"""
    if not events_html:
        events_html = '<tr><td colspan="4" style="padding:10px;color:#667085;font-size:12px">未发现新的可查事件。</td></tr>'

    return f"""
<div style="background:#FFFFFF;border:1px solid #E1E7EF;border-radius:10px;padding:20px 24px;margin-bottom:20px">
  <h2 style="color:#172033;font-size:16px;margin:0 0 6px 0;font-weight:600">国家队动向</h2>
  <p style="color:#667085;font-size:11px;margin:0 0 14px 0">数据源:<a href="{source_url}" style="color:#3B82F6;text-decoration:none">{source_name}</a> | 采集:{fetched_at} | {html.escape(str(national_team.get("scope_note") or "十大股东/十大流通股东为公开披露数据，系统每日检查公开源更新。"))}</p>
  <table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-spacing:8px;margin:0 -8px 10px -8px">
    <tr>{cards}
    </tr>
  </table>
  <table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;background:#FFFFFF;border:1px solid #E1E7EF;border-radius:6px;overflow:hidden">
    <thead>
      <tr style="background:#EEF2F6;color:#5B6472">
        <th style="padding:7px 8px;text-align:left;font-size:11px">日期</th>
        <th style="padding:7px 8px;text-align:left;font-size:11px">主体</th>
        <th style="padding:7px 8px;text-align:left;font-size:11px">事件</th>
        <th style="padding:7px 8px;text-align:left;font-size:11px">来源</th>
      </tr>
    </thead>
    <tbody>{events_html}
    </tbody>
  </table>
</div>"""


def send_notification(scored_stocks, target_date: date,
                      index_gain: float = 0.0,
                      report_path: str = "",
                      zt_list: list = None,
                      audit_results: dict = None,
                      zt_meta: dict = None,
                      index_snapshot: dict = None,
                      national_team: dict = None) -> bool:
    """发送邮件通知"""
    notify_config = _get_notify_config()
    if not notify_config["email_enabled"]:
        logger.info("邮件通知未启用")
        return False

    if zt_list is None:
        zt_list = []
    if audit_results is None:
        audit_results = {}
    if zt_meta is None:
        zt_meta = {}
    if index_snapshot is None:
        index_snapshot = {}
    if national_team is None:
        national_team = {}

    # 按配置排序涨停列表
    sort_by, sort_order = _get_zt_sort_config()
    zt_list = _sort_zt_list(zt_list, sort_by, sort_order)

    # 监控列表条数（与前端一致）
    wl_count = _get_watchlist_count()

    text_content = _build_text_content(scored_stocks, target_date, index_gain, zt_list, wl_count, audit_results, zt_meta, index_snapshot, national_team)
    html_content = _build_html_content(scored_stocks, target_date, index_gain, zt_list, wl_count, sort_by, audit_results, zt_meta, index_snapshot, national_team)
    ok, _ = _send_email(
        subject=f"New France 涨停回撤推荐 - {target_date.strftime('%Y-%m-%d')}",
        text_content=text_content,
        html_content=html_content,
        notify_config=notify_config,
    )
    return ok


# ---------------------------------------------------------------------------
# Plain-text content (fallback for email clients that don't render HTML)
# ---------------------------------------------------------------------------

def _build_text_content(stocks, target_date, index_gain, zt_list: list, wl_count: int = 0, audit_results: dict = None, zt_meta: dict = None, index_snapshot: dict = None, national_team: dict = None) -> str:
    if audit_results is None:
        audit_results = {}
    if zt_meta is None:
        zt_meta = {}
    if index_snapshot is None:
        index_snapshot = {}
    if national_team is None:
        national_team = {}
    date_str = target_date.strftime("%Y-%m-%d")
    weekday = WEEKDAY_CN[target_date.weekday()]

    lines = [
        f"New France 涨停回撤战法 - {date_str} {weekday}",
        "=" * 40,
        f"上证指数当前值: {_format_index_value(index_snapshot)}",
        f"上证指数涨幅: {index_gain:+.2f}%",
        f"指数数据口径: {_format_index_source(index_snapshot)}",
        f"监控股票总数: {wl_count} 只",
        f"今日涨停池有效数: {len(zt_list)} 只",
        f"涨停数据口径: 数据源={zt_meta.get('source','--')} 原始={zt_meta.get('raw_count', len(zt_list))}只 展示={zt_meta.get('final_count', len(zt_list))}只 过滤={zt_meta.get('filtered_count', 0)}只 采集={zt_meta.get('fetched_at','--')}",
        "",
    ]

    if zt_list:
        lines.append("【今日涨停股列表】")
        lines.append("")
        for zt in zt_list:
            mcap = zt.get('mcap', 0)
            mcap_str = f"{mcap/1e8:.1f}亿" if mcap and mcap > 0 else "--"
            fbt_str = _format_seal_time(zt.get('seal_time', 0))
            lines.append(
                f"  {zt['code']} {zt['name']}  "
                f"现价:{zt.get('price',0):.2f}  涨幅:{zt.get('change_pct',0):+.2f}%  "
                f"换手:{zt.get('turnover',0):.1f}%  量比:{zt.get('vol_ratio') or '--'}  "
                f"PE:{zt.get('pe') or '--'}  市值:{mcap_str}  "
                f"封板:{fbt_str}  炸板:{int(zt.get('break_count',0))}次  "
                f"连板:{int(zt.get('consecutive',0))}天"
            )
        lines.append("")

    national_text = _text_national_team_section(national_team)
    if national_text:
        lines.extend(national_text)
        lines.append("")

    strong = [s for s in stocks if s.recommendation == "STRONG_BUY"]
    buy = [s for s in stocks if s.recommendation == "BUY"]
    watch = [s for s in stocks if s.recommendation == "WATCH"]

    lines.append(f"【今日筛选结果】STRONG_BUY {len(strong)} | BUY {len(buy)} | WATCH {len(watch)}")
    if audit_results.get("stocks"):
        lines.append(f"审核通过率: {audit_results.get('pass_rate', 100):.0f}% | 降级: {audit_results.get('downgraded', 0)} 只")
    lines.append("")

    if not stocks:
        lines.append("今日无符合条件的回撤买入信号。")
    else:
        for level_stocks, label in [(strong, "STRONG_BUY 强烈买入"), (buy, "BUY 建议买入"), (watch, "WATCH 观察")]:
            if not level_stocks:
                continue
            lines.append(f"--- {label} ({len(level_stocks)}只) ---")
            lines.append("")
            for s in level_stocks:
                # 查审核结果
                audit_info = {}
                for a in audit_results.get("stocks", []):
                    if a.get("code") == s.code:
                        audit_info = a
                        break

                stars = "★" * min(4, max(1, int(s.adjusted_score / 25)))
                level_cn = {"STRONG_BUY": "强烈买入", "BUY": "建议买入", "WATCH": "观察"}
                lines.append(f"[{level_cn.get(s.recommendation, s.recommendation)}] "
                             f"{s.name}({s.code}) 得分:{s.adjusted_score:.0f} {stars}")
                if audit_info.get("downgraded"):
                    lines.append(f"  ⚠ 审核降级: {audit_info['original_rec']} → {audit_info['adjusted_rec']}")
                    failed = _failed_audit_items(audit_info)
                    if failed:
                        lines.append("  未满足项:")
                        for item in failed:
                            lines.append(f"    - {item.get('name')}: {item.get('detail')}")
                lines.append(f"  回撤:{s.drop_pct:+.2f}% | 排名:#{s.rank}")
                history_rows = _stock_price_history(s)
                if history_rows:
                    lines.append("  价格/回撤走势:")
                    for row in history_rows:
                        lines.append(
                            f"    {row['date']} 收盘:{row['close']:.2f} "
                            f"涨跌:{row['change_pct']:+.2f}% 回撤:{row['drawdown_pct']:+.2f}%"
                        )
                lines.append("  评分详情:")
                for key, r in s.factor_scores.items():
                    if key == "event_bonus":
                        continue
                    mark = "✓" if r.passed else "✗"
                    lines.append(f"    {mark} {r.name}({r.weight*100:.0f}%): {r.detail}")
                lines.append("")

    lines.append("-" * 40)
    lines.append("")
    lines.append("【查看完整数据】")
    lines.append("前端页面: https://new-france.onrender.com")
    lines.append("")
    lines.append("【策略说明】")
    lines.append("1. 每日15:10抓取涨停股池 → 加入监控列表")
    lines.append("2. 次日开始追踪回撤，3-10%回撤区间触发买入信号")
    lines.append("3. 理财有风险，投资需谨慎，本结果仅供参考")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML content (primary — 带样式的表格)
# ---------------------------------------------------------------------------

def _build_html_content(stocks, target_date, index_gain, zt_list: list, wl_count: int = 0, sort_by: str = "seal_time", audit_results: dict = None, zt_meta: dict = None, index_snapshot: dict = None, national_team: dict = None) -> str:
    if audit_results is None:
        audit_results = {}
    if zt_meta is None:
        zt_meta = {}
    if index_snapshot is None:
        index_snapshot = {}
    if national_team is None:
        national_team = {}
    date_str = target_date.strftime("%Y-%m-%d")
    weekday = WEEKDAY_CN[target_date.weekday()]

    strong = [s for s in stocks if s.recommendation == "STRONG_BUY"]
    buy = [s for s in stocks if s.recommendation == "BUY"]
    watch = [s for s in stocks if s.recommendation == "WATCH"]

    parts = [
        _html_head(),
        _html_header(date_str, weekday, index_gain, len(zt_list), wl_count, zt_meta, index_snapshot),
    ]

    # 涨停股列表表格
    if zt_list:
        parts.append(_html_zt_table(zt_list, sort_by, zt_meta))

    parts.append(_html_national_team_section(national_team))

    # 筛选结果
    parts.append(_html_screening_summary(len(strong), len(buy), len(watch), audit_results))

    if not stocks:
        parts.append('<p style="color:#667085;">今日无符合条件的回撤买入信号。</p>')
    else:
        for level_stocks, label, color in [
            (strong, "STRONG_BUY 强烈买入", "#E74C3C"),
            (buy, "BUY 建议买入", "#F39C12"),
            (watch, "WATCH 观察", "#3498DB"),
        ]:
            if not level_stocks:
                continue
            parts.append(_html_recommendation_section(level_stocks, label, color, audit_results))

    parts.append(_html_footer())
    return "\n".join(parts)


def _html_head():
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#F3F6FA;color:#172033;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif">
<div style="max-width:800px;margin:0 auto;padding:24px">"""


def _html_header(date_str, weekday, index_gain, zt_count, wl_count=0, zt_meta=None, index_snapshot=None):
    gain_color = "#E74C3C" if index_gain > 0 else ("#16A36A" if index_gain < 0 else "#667085")
    zt_meta = zt_meta or {}
    index_snapshot = index_snapshot or {}
    index_value = _format_index_value(index_snapshot)
    zt_note = (
        f"数据源:{html.escape(str(zt_meta.get('source', '--')))} | "
        f"原始{zt_meta.get('raw_count', zt_count)}只 | "
        f"展示{zt_meta.get('final_count', zt_count)}只 | "
        f"过滤{zt_meta.get('filtered_count', 0)}只 | "
        f"采集:{html.escape(str(zt_meta.get('fetched_at', '--')))}"
    )
    return f"""
<div style="background:#EEF3F8;border:1px solid #D8E2EC;border-radius:12px;padding:28px 32px;margin-bottom:24px">
  <h1 style="color:#172033;font-size:22px;margin:0 0 4px 0;font-weight:700">New France 涨停回撤战法</h1>
  <p style="color:#667085;font-size:13px;margin:0 0 16px 0">{date_str} {weekday}</p>
  <table cellpadding="0" cellspacing="0" border="0" style="width:100%">
    <tr>
      <td style="padding:12px 20px;background:#FFFFFF;border:1px solid #DCE5EF;border-radius:8px;text-align:center">
        <div style="color:#667085;font-size:11px;margin-bottom:4px">上证指数</div>
        <div style="color:{gain_color};font-size:24px;font-weight:700">{index_value}</div>
        <div style="color:{gain_color};font-size:12px;margin-top:3px">{index_gain:+.2f}%</div>
      </td>
      <td width="12"></td>
      <td style="padding:12px 20px;background:#FFFFFF;border:1px solid #DCE5EF;border-radius:8px;text-align:center">
        <div style="color:#667085;font-size:11px;margin-bottom:4px">监控总数</div>
        <div style="color:#2F87AC;font-size:24px;font-weight:700">{wl_count}<span style="font-size:14px;font-weight:400"> 只</span></div>
      </td>
      <td width="12"></td>
      <td style="padding:12px 20px;background:#FFFFFF;border:1px solid #DCE5EF;border-radius:8px;text-align:center">
        <div style="color:#667085;font-size:11px;margin-bottom:4px">涨停池有效数</div>
        <div style="color:#2F87AC;font-size:24px;font-weight:700">{zt_count}<span style="font-size:14px;font-weight:400"> 只</span></div>
      </td>
    </tr>
  </table>
  <p style="color:#667085;font-size:11px;line-height:1.6;margin:12px 0 0 0">上证指数当前值来自后端真实行情快照，{html.escape(_format_index_source(index_snapshot))}。今日涨停为采集时点的涨停池有效数据，不是 STRONG BUY/BUY/WATCH 筛选通过数。{zt_note}</p>
</div>"""


def _html_zt_table(zt_list, sort_by="seal_time", zt_meta=None):
    """涨停股列表 — 表头深色底色 + 斑马纹 + 当前排序指示"""
    if not zt_list:
        return ""

    # 列定义: (key, label, align)
    columns = [
        ("code", "代码", "left"),
        ("name", "名称", "left"),
        ("price", "现价", "right"),
        ("change_pct", "涨幅", "right"),
        ("turnover", "换手", "right"),
        ("vol_ratio", "量比", "right"),
        ("pe", "PE", "right"),
        ("mcap", "流通市值", "right"),
        ("seal_time", "封板", "center"),
        ("break_count", "炸板", "center"),
        ("consecutive", "连板", "center"),
    ]

    # 表头（当前排序列加箭头指示）
    th_html = ""
    for i, (key, label, align) in enumerate(columns):
        radius_start = "border-radius:6px 0 0 0" if i == 0 else ""
        radius_end = "border-radius:0 6px 0 0" if i == len(columns) - 1 else ""
        arrow = ""
        if key == sort_by:
            arrow = " &#9650;"  # ▲ 升序指示
        th_html += f'<th style="padding:10px;text-align:{align};font-weight:600;{radius_start}{radius_end}">{label}{arrow}</th>\n'

    # 数据行
    rows_html = ""
    for i, zt in enumerate(zt_list):
        bg = "#F8FAFC" if i % 2 == 0 else "#FFFFFF"
        mcap = zt.get('mcap', 0)
        mcap_str = f"{mcap/1e8:.1f}亿" if mcap and mcap > 0 else "--"
        fbt_str = _format_seal_time(zt.get('seal_time', 0))
        chg = zt.get('change_pct', 0)
        chg_color = "#E74C3C" if chg > 0 else "#16A36A"
        rows_html += f"""
    <tr style="background:{bg}">
      <td style="padding:8px 10px;font-family:Menlo,Consolas,monospace;font-size:12px;color:#172033">{zt['code']}</td>
      <td style="padding:8px 10px;font-weight:600;font-size:13px;color:#172033">{zt['name']}</td>
      <td style="padding:8px 10px;text-align:right;font-size:13px;color:#172033">{zt.get('price',0):.2f}</td>
      <td style="padding:8px 10px;text-align:right;color:{chg_color};font-weight:600;font-size:13px">{chg:+.2f}%</td>
      <td style="padding:8px 10px;text-align:right;font-size:13px;color:#172033">{zt.get('turnover',0):.1f}%</td>
      <td style="padding:8px 10px;text-align:right;font-size:13px;color:#172033">{zt.get('vol_ratio') or '--'}</td>
      <td style="padding:8px 10px;text-align:right;font-size:13px;color:#172033">{zt.get('pe') or '--'}</td>
      <td style="padding:8px 10px;text-align:right;font-size:12px;color:#172033">{mcap_str}</td>
      <td style="padding:8px 10px;text-align:center;font-size:12px;color:#172033">{fbt_str}</td>
      <td style="padding:8px 10px;text-align:center;font-size:12px;color:#172033">{int(zt.get('break_count',0))}</td>
      <td style="padding:8px 10px;text-align:center;font-size:12px;color:#172033">{int(zt.get('consecutive',0))}</td>
    </tr>"""

    # 当前排序字段中文名
    sort_label = ZT_SORT_LABELS.get(sort_by, "封板")
    zt_meta = zt_meta or {}
    source_note = (
        f"数据源:{html.escape(str(zt_meta.get('source', '--')))} | "
        f"原始{zt_meta.get('raw_count', len(zt_list))}只 | "
        f"展示{zt_meta.get('final_count', len(zt_list))}只 | "
        f"过滤{zt_meta.get('filtered_count', 0)}只"
    )

    return f"""
<div style="background:#FFFFFF;border:1px solid #E1E7EF;border-radius:10px;padding:20px 24px;margin-bottom:20px">
  <h2 style="color:#172033;font-size:16px;margin:0 0 4px 0;font-weight:600">今日涨停池列表 <span style="color:#667085;font-weight:400;font-size:13px">({len(zt_list)}只)</span></h2>
  <p style="color:#667085;font-size:11px;margin:0 0 6px 0">排序: {sort_label} &#9650; 升序</p>
  <p style="color:#667085;font-size:11px;margin:0 0 14px 0">{source_note}</p>
  <table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;font-size:13px">
    <thead>
      <tr style="background:#E7EDF4;color:#263545">{th_html}
      </tr>
    </thead>
    <tbody>{rows_html}
    </tbody>
  </table>
</div>"""


def _html_screening_summary(strong, buy, watch, audit_results=None):
    total = strong + buy + watch
    pass_rate = audit_results.get("pass_rate", 100) if audit_results else 100
    downgraded = audit_results.get("downgraded", 0) if audit_results else 0
    cards = [
        ("STRONG BUY", strong, "#E74C3C"),
        ("BUY", buy, "#F39C12"),
        ("WATCH", watch, "#3498DB"),
        ("审核通过", f"{pass_rate:.0f}%", "#16A36A" if pass_rate >= 80 else "#E74C3C"),
    ]
    cards_html = ""
    for label, count, color in cards:
        cards_html += f"""
      <td style="padding:14px 16px;background:#F8FAFC;border:1px solid #E1E7EF;border-radius:8px;text-align:center">
        <div style="color:#667085;font-size:11px;margin-bottom:4px">{label}</div>
        <div style="color:{color};font-size:22px;font-weight:700">{count}</div>
      </td>"""

    return f"""
<div style="background:#FFFFFF;border:1px solid #E1E7EF;border-radius:10px;padding:20px 24px;margin-bottom:20px">
  <h2 style="color:#172033;font-size:16px;margin:0 0 16px 0;font-weight:600">今日筛选结果 <span style="color:#667085;font-weight:400;font-size:13px">(共{total}只)</span></h2>
  <table cellpadding="0" cellspacing="0" border="0" style="width:100%">
    <tr>{cards_html}
    </tr>
  </table>
</div>"""


def _html_recommendation_section(level_stocks, label, color, audit_results=None):
    items_html = ""
    for s in level_stocks:
        stars = "★" * min(4, max(1, int(s.adjusted_score / 25)))
        factors_html = ""
        for key, r in s.factor_scores.items():
            if key == "event_bonus":
                continue
            dot_color = "#16A36A" if r.passed else "#D0D5DD"
            factors_html += (
                f'<span style="display:inline-block;width:7px;height:7px;border-radius:50%;'
                f'background:{dot_color};margin-right:4px" '
                f'title="{r.name}: {r.detail} ({r.score:.1f}/10)"></span>'
            )
        drop_color = "#E74C3C" if s.drop_pct < 0 else "#16A36A"
        # 审核降级标记
        downgrade_html = ""
        audit_info = _audit_info_for(audit_results, s.code)
        if audit_info.get("downgraded"):
            failed = _failed_audit_items(audit_info)
            failed_html = ""
            if failed:
                failed_items = "".join(
                    f'<li style="margin:2px 0"><b>{html.escape(str(item.get("name", "")))}</b>: {html.escape(str(item.get("detail", "")))}</li>'
                    for item in failed
                )
                failed_html = f'<div style="margin-top:4px;color:#5B6472">未满足项<ul style="margin:4px 0 0 16px;padding:0">{failed_items}</ul></div>'
            downgrade_html = (
                f'<div style="color:#E74C3C;font-size:11px;margin-top:4px">'
                f'审核降级: {audit_info["original_rec"]} → {audit_info["adjusted_rec"]} '
                f'(失败{audit_info.get("fail_count",0)}项){failed_html}</div>'
            )
        history_html = _html_price_history(_stock_price_history(s))
        items_html += f"""
    <div style="background:#F8FAFC;border:1px solid #E1E7EF;border-radius:8px;padding:16px;margin-bottom:10px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
        <div>
          <span style="font-family:Menlo,Consolas,monospace;color:#3B82F6;font-size:13px">{s.code}</span>
          <span style="color:#172033;font-weight:600;font-size:14px;margin-left:6px">{s.name}</span>
        </div>
        <div style="display:flex;align-items:center;gap:8px">
          <span style="color:{color};font-weight:700;font-size:18px">{s.adjusted_score:.0f}</span>
          <span style="color:#F59E0B;font-size:12px">{stars}</span>
          <span style="background:{color}15;color:{color};padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">{s.recommendation.replace('_',' ')}</span>
        </div>
      </div>
      <div style="font-size:12px;color:#667085;margin-bottom:6px">
        回撤:<span style="color:{drop_color};font-weight:600">{s.drop_pct:+.2f}%</span> &nbsp;|&nbsp;
        涨停日:{s.zt_date} &nbsp;|&nbsp;
        参考价:{s.ref_price:.2f} &nbsp;|&nbsp;
        排名:#{s.rank}
      </div>
      {downgrade_html}
      {history_html}
      <div style="margin-top:4px">{factors_html}</div>
    </div>"""

    return f"""
<div style="background:#FFFFFF;border:1px solid #E1E7EF;border-radius:10px;padding:20px 24px;margin-bottom:20px">
  <h2 style="color:{color};font-size:16px;margin:0 0 14px 0;font-weight:600">{label} <span style="color:#667085;font-weight:400;font-size:13px">({len(level_stocks)}只)</span></h2>
  {items_html}
</div>"""


def _html_price_history(rows: list) -> str:
    if not rows:
        return ""
    cells = ""
    for row in rows:
        draw = row["drawdown_pct"]
        draw_color = "#E74C3C" if draw < 0 else "#16A36A"
        change = row["change_pct"]
        change_color = "#E74C3C" if change > 0 else ("#16A36A" if change < 0 else "#667085")
        cells += f"""
        <tr>
          <td style="padding:4px 6px;color:#5B6472">{html.escape(row['date'])}</td>
          <td style="padding:4px 6px;text-align:right;color:#172033">{row['close']:.2f}</td>
          <td style="padding:4px 6px;text-align:right;color:{change_color}">{change:+.2f}%</td>
          <td style="padding:4px 6px;text-align:right;color:{draw_color};font-weight:600">{draw:+.2f}%</td>
        </tr>"""
    return f"""
      <div style="margin-top:10px">
        <div style="font-size:11px;color:#172033;font-weight:600;margin-bottom:4px">价格/回撤走势</div>
        <table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;font-size:11px;background:#FFFFFF;border:1px solid #E1E7EF;border-radius:6px;overflow:hidden">
          <thead>
            <tr style="background:#EEF2F6;color:#5B6472">
              <th style="padding:5px 6px;text-align:left;font-weight:600">日期</th>
              <th style="padding:5px 6px;text-align:right;font-weight:600">收盘</th>
              <th style="padding:5px 6px;text-align:right;font-weight:600">涨跌</th>
              <th style="padding:5px 6px;text-align:right;font-weight:600">回撤</th>
            </tr>
          </thead>
          <tbody>{cells}
          </tbody>
        </table>
      </div>"""


def _html_footer():
    return """<div style="text-align:center;padding:20px;color:#667085;font-size:12px">
  <p style="margin:0 0 8px 0"><a href="https://new-france.onrender.com" style="color:#3B82F6;text-decoration:none">查看完整数据</a></p>
  <p style="margin:0 0 4px 0">每日15:10自动筛选 | 理财有风险，投资需谨慎</p>
  <p style="margin:0">本结果仅供参考，不构成投资建议</p>
</div>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Email sending (Brevo HTTP API → SMTP fallback)
# ---------------------------------------------------------------------------

def _send_via_brevo(subject: str, text_content: str, html_content: str,
                    notify_config: dict) -> tuple[bool, str]:
    """通过 Brevo HTTP API 发送 HTML 邮件"""
    try:
        import requests
        payload = {
            "sender": {
                "email": notify_config["email_user"] or "noreply@new-france.onrender.com",
                "name": "New France 选股系统",
            },
            "to": [{"email": notify_config["email_to"]}],
            "subject": subject,
            "htmlContent": html_content,
            "textContent": text_content,
        }
        resp = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "api-key": BREVO_API_KEY,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        if resp.status_code in (200, 201):
            logger.info(f"[Brevo] HTML 邮件已发送至 {notify_config['email_to']}")
            return True, "OK"
        err = f"Brevo API 返回 {resp.status_code}: {resp.text[:200]}"
        logger.error(err)
        return False, err
    except Exception as e:
        err = f"Brevo 发送异常: {type(e).__name__}: {e}"
        logger.error(err)
        return False, err


def _send_via_smtp(subject: str, text_content: str, html_content: str,
                   notify_config: dict) -> tuple[bool, str]:
    """通过 SMTP 发送 multipart 邮件（HTML + plain text）"""
    host = notify_config["email_host"]
    port = int(notify_config["email_port"])
    user = notify_config["email_user"]
    password = os.environ.get("SMTP_PASSWORD", SMTP_PASSWORD)
    email_to = notify_config["email_to"]

    if not password:
        return False, "SMTP密码未配置"
    if not user:
        return False, "SMTP_USER 未配置"
    if not email_to:
        return False, "收件邮箱未配置"

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = email_to
        msg.attach(MIMEText(text_content, "plain", "utf-8"))
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        if port in (587, 25):
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.starttls()
                server.login(user, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP_SSL(host, port, timeout=15) as server:
                server.login(user, password)
                server.send_message(msg)

        logger.info(f"[SMTP] HTML 邮件已发送至 {email_to}")
        return True, "OK"
    except smtplib.SMTPAuthenticationError as e:
        err = f"SMTP 登录失败: {user}, 错误={e}"
        logger.error(err)
        return False, err
    except smtplib.SMTPConnectError as e:
        err = f"SMTP 连接失败: {host}:{port}, 错误={e}"
        logger.error(err)
        return False, err
    except OSError as e:
        err = f"SMTP 网络不通: {e}"
        logger.error(err)
        return False, err
    except Exception as e:
        err = f"SMTP 异常: {type(e).__name__}: {e}"
        logger.error(err)
        return False, err


def _send_email(subject: str, text_content: str, html_content: str,
                notify_config: dict = None) -> tuple[bool, str]:
    """发送邮件：Brevo HTTP API 优先 → SMTP 备选"""
    notify_config = notify_config or _get_notify_config()
    if BREVO_API_KEY:
        return _send_via_brevo(subject, text_content, html_content, notify_config)
    return _send_via_smtp(subject, text_content, html_content, notify_config)


def test_email() -> tuple[bool, str]:
    """发送测试邮件"""
    text = "这是一封测试邮件。\n如果收到说明配置正确！\n\nNew France v1.0"
    html = """<!DOCTYPE html><html><body style="font-family:sans-serif;padding:20px">
<h2>New France 测试邮件</h2><p>HTML 邮件配置正确！</p></body></html>"""
    return _send_email(
        subject="[测试] New France 涨停回撤战法",
        text_content=text,
        html_content=html,
    )
