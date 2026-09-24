"""主力资金双向监控服务（plugin 独立版本）。

对外暴露的核心入口：
  - run_principal_capital_scan(...)  ← 给 CLI / Cron / Router 调用
  - read_report() / read_history()    ← 给 Router 读历史
"""
import html
import json
import logging
import math
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

import pandas as pd

# === 唯一对主项目的只读依赖：交易时段判断函数 ===
# 此函数仅返回 dict，不产生副作用，可安全复用
from backend.api.router_system import trading_session_status

# === plugin 内部依赖 ===
from . import intraday_state as intraday
from . import pipeline as pipeline_mod
from .config import (
    CONFIG,
    DATA_DIR,
    HISTORY_FILE,
    INTRADAY_STATE_FILE,
    M5_AUDIT_FILE,
    M5_AUDIT_MAX_RECORDS,
    MANUAL_STATUS_FILE,
    OWNER_CONFLICT_FILE,
    REPORT_DIR,
    REPORT_FILE,
    SHADOW_REPORT_FILE,
    SHADOW_STATE_FILE,
    SNAPSHOT_RAW_BASE,
    SOURCE_HEALTH_FILE,
    atomic_write_json,
    config_fingerprint,
    resolve_execution_mode,
    resolve_pipeline_mode,
)
from .notifier import build_summary_payload, get_smtp_config, send_email
from .sources.multi_source import fetch_market_fund_flow_resilient

logger = logging.getLogger(__name__)

BEIJING_TZ = timezone(timedelta(hours=8))

DIRECTION_BUY = "buy"
DIRECTION_SELL = "sell"
SEVERITY_LEVELS = [("danger", 50.0), ("alert", 40.0), ("warn", 30.0)]


# ---- 工具函数 ----

def _float(value, default=None):
    if value is None or value == "" or value == "-":
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(result):
        return default
    return result


def _json_safe(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _stock_code(value) -> str:
    return str(value or "").strip().zfill(6)


# ---- 票池过滤 ----

def _is_excluded_market(code: str) -> bool:
    return _stock_code(code).startswith(("300", "301"))


def _is_star_market(code: str) -> bool:
    return _stock_code(code).startswith("688")


def _is_main_board(code: str) -> bool:
    c = _stock_code(code)
    # 沪深主板：沪市 60；深市 000/001/002/003（002/003 为深市主板，原先漏含）
    return c.startswith(("60", "000", "001", "002", "003"))


def _is_st_name(name: str) -> bool:
    text = str(name or "").upper()
    return "ST" in text or "*ST" in text or "退" in text


# ---- 去重文件管理 ----

def _notified_file(today: date, direction: str) -> 'Path':
    """当日去重文件路径（使用本模块 DATA_DIR，方便单测 patch）。"""
    from pathlib import Path  # noqa: F401
    return DATA_DIR / f"principal_capital_{direction}_notified_{today.isoformat()}.json"


def load_notified_map(today: date, direction: str) -> Dict[str, list]:
    path = _notified_file(today, direction)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("notified") or {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_notified_map(today: date, direction: str, notified_map: Dict[str, list]) -> None:
    payload = {"date": today.isoformat(), "notified": notified_map}
    atomic_write_json(_notified_file(today, direction), payload)


def cleanup_old_notified(today: date, keep_days: int = 7) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for path in DATA_DIR.glob("principal_capital_*_notified_*.json"):
        try:
            suffix = path.stem.rsplit("_", 1)[-1]
            file_date = date.fromisoformat(suffix)
        except ValueError:
            continue
        if (today - file_date).days > keep_days:
            path.unlink(missing_ok=True)


def should_notify(
    code: str,
    direction: str,
    now: datetime,
    notified_map: Dict[str, list],
    cooldown_minutes: int,
) -> bool:
    del direction
    history = notified_map.get(_stock_code(code), [])
    if not history:
        return True
    last_ts = datetime.fromisoformat(history[-1])
    if last_ts.tzinfo is None:
        last_ts = last_ts.replace(tzinfo=BEIJING_TZ)
    return (now - last_ts) >= timedelta(minutes=cooldown_minutes)


# ---- 阈值过滤 ----

def _base_filter(df: pd.DataFrame, exclude_star: bool = True, min_amount: float = 1e7) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=df.columns if df is not None else [])
    result = df.copy()
    result["code"] = result["code"].map(_stock_code)
    result["name"] = result["name"].fillna("").astype(str)
    result["main_inflow_ratio"] = pd.to_numeric(result["main_inflow_ratio"], errors="coerce")
    result["total_amount"] = pd.to_numeric(result["total_amount"], errors="coerce")
    # 合并为单次布尔掩码，避免空 DataFrame 上逐次布尔过滤触发 pandas 空掩码取列误判。
    # 过滤条件（板块/ST/成交额/阈值）与之前完全一致，仅改变应用方式。
    keep = result["code"].map(_is_main_board)
    keep &= ~result["code"].map(_is_excluded_market)
    if exclude_star:
        keep &= ~result["code"].map(_is_star_market)
    keep &= ~result["name"].map(_is_st_name)
    keep &= result["main_inflow_ratio"].notna()
    amount_mask = result["total_amount"].isna() | (result["total_amount"] >= float(min_amount))
    keep &= amount_mask
    return result[keep].reset_index(drop=True)


def filter_buy_candidates(df, buy_threshold=50.0, exclude_star=True, min_amount=1e7) -> pd.DataFrame:
    result = _base_filter(df, exclude_star=exclude_star, min_amount=min_amount)
    result = result[result["main_inflow_ratio"] >= float(buy_threshold)]
    return result.sort_values("main_inflow_ratio", ascending=False).reset_index(drop=True)


def filter_sell_candidates(df, sell_threshold=30.0, exclude_star=True, min_amount=1e7) -> pd.DataFrame:
    result = _base_filter(df, exclude_star=exclude_star, min_amount=min_amount)
    result = result[result["main_inflow_ratio"] <= -float(sell_threshold)]
    return result.sort_values("main_inflow_ratio", ascending=True).reset_index(drop=True)


def classify_sell_severity(ratio: float) -> str:
    abs_ratio = abs(float(ratio or 0.0))
    for level, threshold in SEVERITY_LEVELS:
        if abs_ratio >= threshold:
            return level
    return "warn"


# ---- 邮件构造 ----

def _format_yi(value) -> str:
    number = _float(value, 0.0) or 0.0
    return f"{number / 1e8:.2f}亿"


def _format_pct(value) -> str:
    number = _float(value)
    return "--" if number is None else f"{number:+.2f}%"


def _format_price(value) -> str:
    """当前价格，保留 2 位小数；缺失显示 --。"""
    number = _float(value)
    return "--" if number is None else f"{number:.2f}"


def _format_time_cn(value) -> str:
    """把 ISO 时间格式化为 YYYY/MM/DD HH:MM:SS（北京时区）。"""
    if not value:
        return "--"
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BEIJING_TZ)
    dt = dt.astimezone(BEIJING_TZ)
    return dt.strftime("%Y/%m/%d %H:%M:%S")


def _staleness_notices(source_status: dict) -> list:
    """据 source_status 生成人类可读的数据滞后提示（可能 0-2 条）。

    - 资金流缓存降级(active_source == cache)：精确到分钟——缓存 TTL 仅 30 分钟，
      分钟级差异直接影响决策价值；
    - 主板清单降级(codes_stale_date 存在)：精确到天——清单只决定扫描范围、变动极慢。
    返回纯文本列表，text 版直接用，HTML 版包一层 banner。
    """
    notices = []
    if source_status.get("active_source") == "cache":
        age = source_status.get("cache_age_seconds")
        if isinstance(age, (int, float)):
            minutes = max(1, int(round(age / 60)))
            notices.append(f"资金流为约 {minutes} 分钟前的缓存数据，非实时，请谨慎参考。")
        else:
            notices.append("资金流为缓存降级数据，非实时，请谨慎参考。")
    codes_stale_date = source_status.get("codes_stale_date")
    if codes_stale_date:
        notices.append(
            f"主板股票清单为 {codes_stale_date} 的旧数据（当日实时清单拉取失败），"
            "可能漏掉此后新上市个股，信号本身仍为实时。"
        )
    return notices


def build_email_payload(
    buy_fresh: pd.DataFrame,
    sell_fresh: pd.DataFrame,
    now: datetime,
    source_status: dict,
    include_buy: bool = True,
) -> Tuple[str, str, str]:
    buy_count = len(buy_fresh)
    sell_count = len(sell_fresh)
    danger_count = sum(1 for _, row in sell_fresh.iterrows() if row.get("severity") == "danger")
    stamp = now.strftime("%Y-%m-%d %H:%M")
    if not include_buy:
        # 降级模式：买入区不再进邮件（仅作雷达数据源），标题弱化为「行情参考」
        # 明确低优先级，把注意力让给「盘中雷达」邮件
        subject = f"[行情参考] 主力派发 {sell_count} 只 - {stamp}"
    elif buy_count and sell_count:
        suffix = f" [DANGER级×{danger_count}]" if danger_count else ""
        subject = f"买{buy_count}卖{sell_count}{suffix} - {stamp}"
    elif buy_count:
        subject = f"买入信号 {buy_count} 只 - {stamp}"
    else:
        subject = f"卖出信号 {sell_count} 只 - {stamp}"

    def text_lines(df: pd.DataFrame, title: str, include_severity: bool = False) -> str:
        if df.empty:
            return f"{title}\n暂无\n"
        lines = [title]
        for _, row in df.iterrows():
            parts = [
                _stock_code(row.get("code")),
                str(row.get("name", "")),
                f"价{_format_price(row.get('price'))}",
                f"时间{_format_time_cn(row.get('flow_time'))}",
                f"占比{_format_pct(row.get('main_inflow_ratio'))}",
                f"主力{_format_yi(row.get('main_net_inflow'))}",
                f"成交{_format_yi(row.get('total_amount'))}",
                f"涨幅{_format_pct(row.get('change_pct'))}",
            ]
            if include_severity:
                parts.append(f"级别{row.get('severity', '--')}")
            lines.append(" | ".join(parts))
        return "\n".join(lines) + "\n"

    notices = _staleness_notices(source_status)
    if include_buy:
        text = text_lines(buy_fresh, "买入区") + "\n" + text_lines(sell_fresh, "卖出区", include_severity=True)
    else:
        text = text_lines(sell_fresh, "卖出区", include_severity=True)
    if notices:
        text += "\n【数据滞后提示】\n" + "\n".join(f"· {n}" for n in notices) + "\n"
    text += f"\n数据源: {source_status.get('active_source', '--')} / stale={source_status.get('is_stale', False)}\n"

    def html_rows(df: pd.DataFrame) -> str:
        if df.empty:
            return '<tr><td colspan="9">暂无</td></tr>'
        rows = []
        for _, row in df.iterrows():
            severity = row.get("severity", "")
            extra_style = {
                "danger": "background:#dbeafe;color:#0f172a;font-weight:700;",
                "alert": "background:#eff6ff;color:#1d4ed8;",
                "warn": "background:#f1f5f9;color:#475569;",
            }.get(severity, "")
            rows.append(
                "<tr>"
                f"<td>{html.escape(_stock_code(row.get('code')))}</td>"
                f"<td>{html.escape(str(row.get('name', '')))}</td>"
                f"<td>{html.escape(_format_price(row.get('price')))}</td>"
                f"<td>{html.escape(_format_time_cn(row.get('flow_time')))}</td>"
                f"<td>{html.escape(_format_pct(row.get('main_inflow_ratio')))}</td>"
                f"<td>{html.escape(_format_yi(row.get('main_net_inflow')))}</td>"
                f"<td>{html.escape(_format_yi(row.get('total_amount')))}</td>"
                f"<td>{html.escape(_format_pct(row.get('change_pct')))}</td>"
                f"<td style='{extra_style}'>{html.escape(str(severity or '--'))}</td>"
                "</tr>"
            )
        return "".join(rows)

    stale_banner = ""
    if notices:
        items = "".join(f"<li>{html.escape(n)}</li>" for n in notices)
        stale_banner = (
            "<div style='padding:10px 12px;background:#fee2e2;color:#b91c1c;"
            "border-radius:8px;margin-bottom:12px'><strong>数据滞后提示</strong>"
            f"<ul style='margin:6px 0 0;padding-left:20px'>{items}</ul></div>"
        )
    if include_buy:
        buy_section = f"""<h3 style="color:#b91c1c">买入区</h3>
<table style="width:100%;border-collapse:collapse;margin-bottom:16px" border="1" cellspacing="0" cellpadding="8">
<tr style="background:#fef2f2"><th>代码</th><th>名称</th><th>价格</th><th>时间</th><th>占比</th><th>主力净流入</th><th>成交额</th><th>涨幅</th><th>级别</th></tr>
{html_rows(buy_fresh)}
</table>"""
    else:
        buy_section = (
            "<p style='margin:0 0 16px;padding:10px 12px;background:#f8fafc;color:#64748b;"
            "border-radius:8px;font-size:13px'>本邮件为主力资金<strong>派发参考</strong>（低优先级）。"
            "重要买入信号请以「盘中雷达」邮件为准。</p>"
        )
    html_content = f"""<!DOCTYPE html>
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:20px;color:#111827">
<h2 style="margin:0 0 12px">主力资金双向监控</h2>
{stale_banner}
{buy_section}
<h3 style="color:#1d4ed8">卖出区</h3>
<table style="width:100%;border-collapse:collapse" border="1" cellspacing="0" cellpadding="8">
<tr style="background:#eff6ff"><th>代码</th><th>名称</th><th>价格</th><th>时间</th><th>占比</th><th>主力净流入</th><th>成交额</th><th>涨幅</th><th>级别</th></tr>
{html_rows(sell_fresh)}
</table>
<p style="margin-top:16px;color:#6b7280">数据源: {html.escape(str(source_status.get('active_source', '--')))} | stale={html.escape(str(source_status.get('is_stale', False)))}</p>
</body></html>"""
    return subject, text, html_content


# ---- 报告落盘 ----

def write_report(payload: dict) -> None:
    atomic_write_json(REPORT_FILE, payload)


def read_report() -> dict:
    if not REPORT_FILE.exists():
        return {"status": "empty", "buy_triggered": [], "sell_triggered": []}
    try:
        return json.loads(REPORT_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "empty", "buy_triggered": [], "sell_triggered": []}


def read_history() -> dict:
    if not HISTORY_FILE.exists():
        return {"records": []}
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"records": []}


# ---- data-snapshots 远程回退（Render 自身取不到东财，读快照分支保证与邮件同源）----

def _fetch_snapshot_json(filename: str, directory: str = "reports") -> Optional[dict]:
    """远程回退统一走 snapshot_store 合并缓存（single-flight/退避/条件GET/SWR），失败返回 None。"""
    from backend.services.snapshot_store import RemotePolicy, fetch_remote_snapshot

    key = filename.replace(".json", "").replace("/", "_")
    ttl = 60.0 if "source_health" in filename else 300.0
    entry = fetch_remote_snapshot(key, f"{SNAPSHOT_RAW_BASE}/{directory}/{filename}", RemotePolicy(ttl_seconds=ttl))
    if entry is None:
        return None
    try:
        payload = dict(entry.parsed())  # 复制后再附加元数据，不污染共享解析对象
    except Exception as exc:  # noqa: BLE001
        logger.info("主力资金远程快照解析失败(%s): %s", filename, exc)
        return None
    payload["_source"] = "snapshot"
    if entry.stale:
        payload["stale"] = True
        payload["fetched_at"] = entry.fetched_at
        payload["refresh_error"] = entry.refresh_error
    return payload


def _normalize_report_payload(payload: Optional[dict]) -> dict:
    """补齐前端区分任务状态所需的稳定字段。"""
    normalized = dict(payload or {})
    normalized.setdefault("status", "empty")
    normalized.setdefault("now", None)
    normalized.setdefault("reason", str(normalized.get("error") or ""))
    source_status = normalized.get("source_status")
    if not isinstance(source_status, dict):
        source_status = {"active_source": "none"}
    else:
        source_status = dict(source_status)
        source_status.setdefault("active_source", "none")
    normalized["source_status"] = source_status
    normalized.setdefault("buy_triggered", [])
    normalized.setdefault("sell_triggered", [])
    return normalized


def _check_report_consistency(report: dict) -> dict:
    """P1-7：读取组合数据时核验 state.last_batch_id == report.batch_id。"""
    # P1-R4：所有带 batch_id 的 official 报告都执行一致性检查
    if not report or not report.get("batch_id"):
        return report
    try:
        state = intraday.load_state(trade_date=report.get("trade_date"))
        problems = []
        if state.get("schema_version") != 2:
            problems.append("state_schema_incompatible")
        if state.get("_source_unavailable") or state.get("warming_reason") == "corrupt_state_recovered":
            problems.append("state_unavailable_or_recovering")
        last_batch_id = state.get("last_batch_id")
        if not last_batch_id:
            problems.append("state_last_batch_id_empty")
        elif last_batch_id != report.get("batch_id"):
            problems.append("state_report_batch_mismatch")
        if report.get("trade_date") and state.get("trade_date") and report.get("trade_date") != state.get("trade_date"):
            problems.append("state_report_trade_date_mismatch")
        if problems:
            report = dict(report)
            report["consistency_error"] = True
            report["consistency_problems"] = problems
            report["status"] = "degraded"
            report.setdefault("reason", "state_report_inconsistent")
    except Exception:
        pass
    return report


def read_report_resilient() -> dict:
    """读最新报告：本地带 batch_id 的报告优先（含一致性核验），否则回退远程快照。

    远程快照不与本地 state 组合做一致性判定（避免把无关 runner 的 state 与快照混合）。
    """
    local = _normalize_report_payload(read_report())
    if local.get("batch_id"):
        return _check_report_consistency(local)
    remote = _fetch_snapshot_json("principal_capital_latest.json")
    if remote and remote.get("status"):
        remote = _normalize_report_payload(remote)
        remote["_source"] = "snapshot"
        return remote
    return local


def read_source_health_resilient() -> dict:
    """优先读取本地健康状态，Render 本地为空时回退到快照分支。"""
    if SOURCE_HEALTH_FILE.exists():
        try:
            local = json.loads(SOURCE_HEALTH_FILE.read_text(encoding="utf-8"))
            if isinstance(local, dict):
                return local
        except (json.JSONDecodeError, OSError):
            pass
    remote = _fetch_snapshot_json(
        "principal_capital_source_health.json",
        directory="data",
    )
    if isinstance(remote, dict):
        remote["_source"] = "snapshot"
        return remote
    return {"sources": {}}


def read_history_resilient() -> dict:
    """读历史：本地有记录直接返回，否则回退到 data-snapshots 快照。"""
    local = read_history()
    if local.get("records"):
        return local
    remote = _fetch_snapshot_json("principal_capital_history.json")
    if remote and remote.get("records"):
        return remote
    return local


def append_history(result: dict, max_records: int = 1000) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    current = read_history()
    records = current.get("records") or []
    ts = result.get("now")
    for item in result.get("buy_triggered", []):
        records.append({
            "ts": ts, "direction": DIRECTION_BUY,
            "flow_time": item.get("flow_time") or ts,
            "code": item.get("code"), "name": item.get("name"),
            "ratio": item.get("main_inflow_ratio"),
            "main_net": item.get("main_net_inflow"),
            "amount": item.get("total_amount"),
            "change_pct": item.get("change_pct"),
        })
    for item in result.get("sell_triggered", []):
        records.append({
            "ts": ts, "direction": DIRECTION_SELL,
            "flow_time": item.get("flow_time") or ts,
            "code": item.get("code"), "name": item.get("name"),
            "ratio": item.get("main_inflow_ratio"),
            "severity": item.get("severity"),
            "main_net": item.get("main_net_inflow"),
            "amount": item.get("total_amount"),
            "change_pct": item.get("change_pct"),
        })
    atomic_write_json(HISTORY_FILE, {"records": records[-max_records:]})


# ---- 主流程 ----

def _fresh_rows(df: pd.DataFrame, direction: str, now: datetime,
                notified_map: Dict[str, list], cooldown_minutes: int) -> pd.DataFrame:
    rows = []
    for _, row in df.iterrows():
        if should_notify(row.get("code"), direction, now, notified_map, cooldown_minutes):
            row_dict = row.to_dict()
            row_dict["flow_time"] = now.isoformat()
            if direction == DIRECTION_SELL:
                row_dict["severity"] = classify_sell_severity(row.get("main_inflow_ratio"))
            rows.append(row_dict)
    return pd.DataFrame(rows)


def _update_notified_map(notified_map: Dict[str, list], df: pd.DataFrame,
                         now: datetime) -> Dict[str, list]:
    for _, row in df.iterrows():
        code = _stock_code(row.get("code"))
        notified_map.setdefault(code, []).append(now.isoformat())
    return notified_map


def _empty_result(status: str, reason: str, now: datetime, buy_threshold: float,
                  sell_threshold: float, source_status: Optional[dict] = None,
                  execution_mode: str = "readonly", pipeline_mode: str = "strict",
                  owner_id: Optional[str] = None) -> dict:
    return {
        "schema_version": 2,
        "status": status,
        "reason": reason,
        "trade_date": now.date().isoformat(),
        "now": now.isoformat(),
        "batch_id": None,
        "owner_id": owner_id,
        "execution_mode": execution_mode,
        "pipeline_mode": pipeline_mode,
        "deadline_met": None,
        "quality": {"status": "provisional", "notify_eligible": False, "degraded_reasons": []},
        "universe": {"source": "none", "count": 0, "stale": False},
        "bulk": {"status": "not_run", "rows": 0, "main_board_rows": 0, "admitted": False,
                 "candidate_count": 0, "latency_ms": None},
        "refine": {"status": "not_run", "requested_count": 0, "received_count": 0,
                   "missing_codes": [], "coverage_ratio": None, "latency_ms": None},
        "audit": {"kind": "shadow_truth", "truth_buy_count": 0, "truth_sell_count": 0,
                  "false_negative_buy": [], "false_negative_sell": []},
        "thresholds": {"buy": buy_threshold, "sell": sell_threshold},
        "source_status": source_status or {"active_source": "none"},
        "scanned": 0,
        "buy_candidates": 0, "sell_candidates": 0,
        "buy_fresh_count": 0, "sell_fresh_count": 0,
        "email_sent": False, "email_error": None,
        "buy_triggered": [], "sell_triggered": [],
        "buy_candidates_current": [], "sell_candidates_current": [],
        "buy_candidates_today": [], "sell_candidates_today": [],
    }


def _evaluate_quality(source_status: dict, coverage_ratio, has_source_time: bool) -> dict:
    """行级质量门控（P1-6）：按覆盖率 + 源时间契约判定，不按供应商名称放行。"""
    active = (source_status or {}).get("active_source", "none")
    stale = bool((source_status or {}).get("is_stale", False))
    if active == "cache" or stale:
        return {"status": "degraded", "notify_eligible": False, "degraded_reasons": ["stale_cache"]}
    if coverage_ratio is not None and coverage_ratio < 1.0:
        return {"status": "degraded", "notify_eligible": False, "degraded_reasons": ["incomplete_coverage"]}
    if has_source_time:
        return {"status": "accepted", "notify_eligible": True, "degraded_reasons": []}
    return {
        "status": "provisional",
        "notify_eligible": bool(CONFIG["allow_provisional_notify"]),
        "degraded_reasons": ["fund_source_time_unavailable"],
    }


def _determine_status(coverage_ratio, quality: dict, source_status: dict) -> str:
    """P0-2：completed 只允许完整覆盖且质量门禁通过；否则 partial/degraded。"""
    if (source_status or {}).get("active_source") == "cache" or (source_status or {}).get("is_stale"):
        return "degraded"
    if quality.get("status") == "degraded":
        return "degraded"
    if coverage_ratio is None or coverage_ratio < 1.0:
        return "partial"
    return "completed"


def _radar_pool_cfg() -> dict:
    """读取 smart_money_radar 的池参数（懒导入，避免循环依赖）。"""
    try:
        from backend.plugins.smart_money_radar.config import CONFIG as RADAR_CONFIG
        keys = (
            "radar_pool_max", "radar_pool_min_dwell_min", "radar_pool_protected_cap",
            "radar_pool_rotation_seats", "radar_pool_max_stale_min",
        )
        return {key: RADAR_CONFIG[key] for key in keys}
    except Exception:
        return {}


def write_shadow_report(payload: dict) -> None:
    atomic_write_json(SHADOW_REPORT_FILE, payload)


def write_manual_status(payload: dict) -> None:
    atomic_write_json(MANUAL_STATUS_FILE, payload)


def write_owner_conflict_diagnostic(payload: dict) -> None:
    atomic_write_json(OWNER_CONFLICT_FILE, payload)


def clear_stale_owner_conflict(trade_date: str) -> None:
    """跨日清理历史 owner_conflict 诊断，避免 watchdog 对陈旧文件反复误报。

    该诊断文件一旦写入就会随 data-snapshots 分支长期滞留；只有当冲突发生在
    本交易日时才应保留。official 成功接管 owner 时（本日已无冲突）顺带清理
    不属于今日的残留诊断。
    """
    if not OWNER_CONFLICT_FILE.exists():
        return
    try:
        payload = json.loads(OWNER_CONFLICT_FILE.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and payload.get("trade_date") == trade_date:
            return  # 今日冲突诊断保留
    except (json.JSONDecodeError, OSError):
        pass
    try:
        OWNER_CONFLICT_FILE.unlink(missing_ok=True)
        logger.info("清理跨日 owner_conflict 诊断：%s", OWNER_CONFLICT_FILE.name)
    except OSError:
        pass


def read_intraday_state() -> dict:
    """读取日内状态（供雷达/前端/诊断复用）。"""
    return intraday.load_state()


def _fetch_strict_truth(now) -> tuple:
    """P0-1：strict 真值固定为完整 sina_full 同批数据。

    抓取前生成权威主板 universe（requested_codes），返回后计算 received/missing/coverage。
    返回 (df, source_status, truth_meta)。
    """
    from .sources.sina import fetch_codes_fund_flow_sina_detailed
    from .sources.sina_market import fetch_main_board_universe, get_last_codes_stale_date

    universe = fetch_main_board_universe()
    requested = [str(code).zfill(6) for code in universe.get("codes") or []]
    stale_date = get_last_codes_stale_date()
    rows, rejected = fetch_codes_fund_flow_sina_detailed(
        requested,
        max_workers=int(CONFIG["sina_max_workers"]),
        timeout=int(CONFIG["refine_timeout_seconds"]),
        batch_timeout=float(CONFIG["round_deadline_seconds"]),
        now=now,
    )
    received_codes = sorted({str(row.get("code") or "").zfill(6) for row in rows if row.get("code")})
    missing = sorted(set(requested) - set(received_codes))
    coverage = round(len(received_codes) / len(requested), 6) if requested else 0.0
    df = pd.DataFrame(rows) if rows else pd.DataFrame()
    universe_verified = bool(universe.get("verified"))
    source_status = {
        "active_source": "sina_full",
        "is_stale": False,
        "codes_stale_date": stale_date,
        "invalid_rows": len(rejected),
        "universe_verified": universe_verified,
    }
    truth = {
        "source": "sina_full",
        "requested_codes": requested,
        "received_codes": received_codes,
        "missing_codes": missing,
        "coverage_ratio": coverage,
        "rejected_rows": rejected,
        "has_source_time": False,  # 新浪单股无 source_time
        "universe_verified": universe_verified,
        "universe_meta": universe,
        "valid_for_admission": bool(rows and not missing and not rejected and universe_verified),
    }
    return df, source_status, truth


def _fetch_truth_with_fallback(now, enable_verify) -> tuple:
    """strict 主路径；sina_full 失败才走备用源并标 strict_fallback（不计入 M5）。"""
    df, source_status, truth = _fetch_strict_truth(now)
    if df is not None and not df.empty:
        return df, source_status, truth, "strict"
    df, source_status = fetch_market_fund_flow_resilient(enable_verify=enable_verify)
    truth = {
        "source": (source_status or {}).get("active_source", "none"),
        "requested_codes": [],
        "received_codes": [str(code).zfill(6) for code in df["code"].tolist()] if not df.empty else [],
        "missing_codes": [],
        "coverage_ratio": None,
        "rejected_rows": {},
        "has_source_time": False,
        "valid_for_admission": False,
    }
    return df, source_status, truth, "strict_fallback"


def _record_m5_audit(result: dict, truth: dict, elapsed_seconds: float) -> None:
    """P1-3：保存有界的每日 M5 审计记录（可逐轮回放；只有 valid_for_admission 计入验收）。"""
    records = []
    if M5_AUDIT_FILE.exists():
        try:
            payload = json.loads(M5_AUDIT_FILE.read_text(encoding="utf-8"))
            records = payload.get("records") or []
        except (json.JSONDecodeError, OSError):
            records = []
    audit = result.get("audit") or {}
    record = {
        "batch_id": result.get("batch_id"),
        "trade_date": result.get("trade_date"),
        "session": result.get("session"),
        "now": result.get("now"),
        "truth_source": truth.get("source"),
        "universe_count": len(truth.get("requested_codes") or []),
        "requested_count": len(truth.get("requested_codes") or []),
        "received_count": len(truth.get("received_codes") or []),
        "coverage_ratio": truth.get("coverage_ratio"),
        "config_fingerprint": config_fingerprint(),
        "thresholds": result.get("thresholds"),
        "candidate_summary": {
            "buy": result.get("buy_candidates"),
            "sell": result.get("sell_candidates"),
        },
        "false_negative_buy": audit.get("false_negative_buy", []),
        "false_negative_sell": audit.get("false_negative_sell", []),
        "latency_ms": int(elapsed_seconds * 1000),
        "deadline_met": result.get("deadline_met"),
        "valid_for_admission": bool(result.get("round_valid")),
    }
    records.append(record)
    atomic_write_json(M5_AUDIT_FILE, {"records": records[-M5_AUDIT_MAX_RECORDS:]})


def _run_bulk_shadow(now, df, universe_codes, state, buy_threshold, sell_threshold, exclude_star):
    """strict 全量结果作为真值，对 bulk 漏斗做零额外精算请求的影子比较。

    返回 (bulk_meta, audit, next_audit_cursor)。
    """
    bulk_meta = {
        "status": "shadow_only", "rows": 0, "main_board_rows": 0, "admitted": bool(CONFIG["bulk_admitted"]),
        "candidate_count": 0, "latency_ms": None, "validation": None, "error": None,
    }
    audit = {
        "kind": "shadow_truth", "truth_buy_count": 0, "truth_sell_count": 0,
        "false_negative_buy": [], "false_negative_sell": [],
        "false_positive_buy": [], "false_positive_sell": [],
        "valid_for_admission": False,
    }
    next_cursor = int((state or {}).get("audit_cursor", 0))
    try:
        from .sources.sina_market import fetch_bulk_fund_flow, parse_bulk_rows, validate_bulk_rows
        payload, latency_ms = fetch_bulk_fund_flow()
        rows = parse_bulk_rows(payload, now)
        universe = {_stock_code(code) for code in (universe_codes or [])}
        validation = validate_bulk_rows(rows, universe)
        bulk_meta["rows"] = len(rows)
        bulk_meta["main_board_rows"] = len([row for row in rows if row["code"] in universe])
        bulk_meta["latency_ms"] = latency_ms
        bulk_meta["validation"] = validation

        candidates = (state or {}).get("candidates") or {}
        previous_current = [key.split(":", 1)[1] for key, entry in candidates.items()
                            if ":" in key and entry.get("is_current")]
        dwell_codes = list((state or {}).get("pool_entries") or {})
        # 补集审计只从「universe - 粗筛(不含 audit)」取样
        coarse_without = pipeline_mod.build_coarse_union(
            rows, previous_current, dwell_codes, [], CONFIG
        )["codes"]
        audit_codes, next_cursor = pipeline_mod.complement_audit_codes(
            universe, coarse_without, (state or {}).get("audit_cursor", 0), CONFIG
        )
        coarse = pipeline_mod.build_coarse_union(rows, previous_current, dwell_codes, audit_codes, CONFIG)
        bulk_meta["candidate_count"] = len(coarse["codes"])
        bulk_meta["audit_codes"] = audit_codes
        audit = pipeline_mod.compare_with_truth(coarse["codes"], df, {
            "buy": lambda frame: filter_buy_candidates(frame, buy_threshold=buy_threshold, exclude_star=exclude_star),
            "sell": lambda frame: filter_sell_candidates(frame, sell_threshold=sell_threshold, exclude_star=exclude_star),
        })
    except Exception as exc:  # noqa: BLE001 bulk shadow 失败不影响 strict 正式结果
        logger.info("bulk shadow 比较失败: %s", exc)
        bulk_meta["status"] = "shadow_error"
        bulk_meta["error"] = f"{type(exc).__name__}: {exc}"
    return bulk_meta, audit, next_cursor


def _status_reason(status: str, coverage_ratio, deadline_met: bool) -> str:
    if status == "completed":
        return ""
    if status == "partial":
        if not deadline_met:
            return "deadline_exceeded"
        return f"coverage_incomplete:{coverage_ratio}"
    if status == "degraded":
        return "degraded_quality"
    return status


def run_principal_capital_scan(
    now: Optional[datetime] = None,
    buy_threshold: float = 50.0,
    sell_threshold: float = 30.0,
    exclude_star: bool = True,
    sell_cooldown_minutes: int = 60,
    enable_verify: bool = False,
    dry_run: bool = False,
    force: bool = False,
    execution_mode: Optional[str] = None,
    pipeline_mode: Optional[str] = None,
    owner_id: Optional[str] = None,
    enable_shadow: Optional[bool] = None,
    batch_id: Optional[str] = None,
) -> dict:
    """执行主力资金双向扫描（23 v2：strict=sina_full 同批真值 + bulk shadow 对照）。

    - execution_mode 默认 readonly；official 是唯一写者。
    - hybrid 在 M6 前代码层拒绝（resolve_pipeline_mode 抛 RuntimeError）。
    """
    now = now or datetime.now(BEIJING_TZ)
    now = intraday._ensure_aware(now)  # naive datetime 直接抛 ValueError（P1-1）
    execution_mode = resolve_execution_mode(execution_mode)
    requested_pipeline_mode = (pipeline_mode or CONFIG["pipeline_mode"]).strip().lower()
    effective_pipeline_mode = resolve_pipeline_mode(requested_pipeline_mode)  # hybrid -> RuntimeError
    owner_id = owner_id or CONFIG["official_owner"]

    if execution_mode == "official" and not owner_id:
        result = _empty_result("owner_conflict", "PC_OFFICIAL_OWNER 未配置", now,
                               buy_threshold, sell_threshold, execution_mode=execution_mode,
                               pipeline_mode=effective_pipeline_mode)
        write_owner_conflict_diagnostic(result)  # P1-4：冲突方不得写 official 报告
        return result

    session = trading_session_status(now)
    if not force and not session.get("is_trading_hours"):
        result = _empty_result("skipped", session.get("market_status_text", "非交易时段"),
                               now, buy_threshold, sell_threshold, execution_mode=execution_mode,
                               pipeline_mode=effective_pipeline_mode, owner_id=owner_id)
        if execution_mode == "official":
            write_report(result)
        return result

    today = now.date()
    # P1-2：shadow 使用独立状态；readonly 不读旧 official 状态，基于本轮结果自洽
    if execution_mode == "official":
        state = intraday.load_state(trade_date=today.isoformat(), now=now)
    elif execution_mode == "shadow":
        state = intraday.load_state(path=SHADOW_STATE_FILE, trade_date=today.isoformat(), now=now)
    else:
        state = intraday.empty_state(today.isoformat(), pipeline_mode=effective_pipeline_mode)

    round_ctx = pipeline_mod.build_round_context(
        owner_id=owner_id, execution_mode=execution_mode, pipeline_mode=effective_pipeline_mode,
        trade_date=today.isoformat(), started_at=now,
        deadline_at=now + timedelta(seconds=int(CONFIG["round_deadline_seconds"])),
    )
    if batch_id:
        round_ctx["batch_id"] = batch_id
    batch_id = round_ctx["batch_id"]
    started_monotonic = time.monotonic()
    session_label = "am" if now.hour < 12 else "pm"

    if execution_mode == "official":
        ok, state, conflict = intraday.acquire_owner_atomic(
            INTRADAY_STATE_FILE, owner_id, int(CONFIG["owner_lease_seconds"]), now
        )
        if not ok:
            result = _empty_result("owner_conflict", conflict or "owner_conflict", now,
                                   buy_threshold, sell_threshold, execution_mode=execution_mode,
                                   pipeline_mode=effective_pipeline_mode, owner_id=owner_id)
            write_owner_conflict_diagnostic(result)
            return result
        clear_stale_owner_conflict(today.isoformat())
        cleanup_old_notified(today)
        buy_map = load_notified_map(today, DIRECTION_BUY)
        sell_map = load_notified_map(today, DIRECTION_SELL)
    else:
        buy_map, sell_map = {}, {}

    is_replay = (state.get("last_batch_id") == batch_id)

    df, source_status, truth, effective_pipeline_mode = _fetch_truth_with_fallback(now, enable_verify)
    if df.empty:
        result = _empty_result("no_data", "", now, buy_threshold, sell_threshold, source_status,
                               execution_mode=execution_mode, pipeline_mode=effective_pipeline_mode,
                               owner_id=owner_id)
        if execution_mode == "official":
            write_report(result)
        return result

    requested = truth.get("requested_codes") or []
    received = truth.get("received_codes") or []
    missing = truth.get("missing_codes") or []
    coverage_ratio = truth.get("coverage_ratio")
    rejected_rows = truth.get("rejected_rows") or {}
    has_source_time = bool(truth.get("has_source_time", False))
    quality = _evaluate_quality(source_status, coverage_ratio, has_source_time)

    buy_cands = filter_buy_candidates(df, buy_threshold=buy_threshold, exclude_star=exclude_star)
    sell_cands = filter_sell_candidates(df, sell_threshold=sell_threshold, exclude_star=exclude_star)

    truth_latency_ms = int((time.monotonic() - started_monotonic) * 1000)
    if enable_shadow is None:
        enable_shadow = execution_mode in ("official", "shadow")
    bulk_meta, audit, next_audit_cursor = None, None, None
    if enable_shadow:
        bulk_meta, audit, next_audit_cursor = _run_bulk_shadow(
            now, df, requested, state, buy_threshold, sell_threshold, exclude_star
        )
    bulk_latency_ms = int((time.monotonic() - started_monotonic) * 1000) - truth_latency_ms
    # P1-R1：deadline 在 truth + bulk 结束后计算（含 bulk 耗时），并统一用于 status/fallback/M5
    elapsed_seconds = time.monotonic() - started_monotonic
    deadline_met = elapsed_seconds <= float(CONFIG["round_deadline_seconds"])

    buy_fresh = _fresh_rows(buy_cands, DIRECTION_BUY, now, buy_map, CONFIG["buy_dedup_minutes"])
    sell_fresh = _fresh_rows(sell_cands, DIRECTION_SELL, now, sell_map, sell_cooldown_minutes)

    email_sent = False
    email_error = None
    notify_eligible = execution_mode == "official" and quality["notify_eligible"]
    if notify_eligible and not sell_fresh.empty and not dry_run:
        subject, text, html_content = build_email_payload(
            buy_fresh, sell_fresh, now, source_status, include_buy=False
        )
        email_sent, email_error = send_email(subject, text, html_content, get_smtp_config())

    status = _determine_status(coverage_ratio, quality, source_status)
    batch_is_partial = status != "completed"

    buy_records = buy_cands.to_dict("records")
    sell_records = sell_cands.to_dict("records")
    batch_meta = {
        "batch_id": batch_id, "now": now.isoformat(),
        "is_partial": batch_is_partial, "pipeline_mode": effective_pipeline_mode,
    }

    auto_fallback = None
    features = {}
    sentinel_label = None
    if execution_mode in ("official", "shadow"):
        state["candidates"] = intraday.merge_candidate_state(state, buy_records, sell_records, batch_meta)
        state["last_batch_id"] = batch_id
        state["pipeline_mode"] = effective_pipeline_mode
        if next_audit_cursor is not None and not is_replay:
            state["audit_cursor"] = next_audit_cursor

        # P1-R5：sentinel 接入主路径（strict 全量即完整真值；标记 done 防重复）
        sentinel_label = intraday.should_run_sentinel(state, now, CONFIG["sentinel_times"])
        if sentinel_label:
            state = intraday.mark_sentinel_done(state, sentinel_label)

        pool_codes = set((state.get("pool_entries") or {}).keys())
        fund_codes = {_stock_code(row.get("code")) for row in buy_records + sell_records} | pool_codes
        fund_batch_meta = {
            "batch_id": batch_id, "observed_at": now.isoformat(),
            "source_segment": f"{source_status.get('active_source', 'sina_single')}:v1:{today.isoformat()}",
            "trade_date": today.isoformat(), "is_partial": batch_is_partial,
            "is_stale": bool(source_status.get("is_stale", False)),
            "is_cache": source_status.get("active_source") == "cache",
        }
        refined_by_code = {_stock_code(row["code"]): row for _, row in df.iterrows()}
        for code in fund_codes:
            row = refined_by_code.get(code)
            if row is None:
                continue
            state["fund_series"] = intraday.append_fund_observation(
                state.get("fund_series") or {}, row, fund_batch_meta, CONFIG
            )
            features[code] = intraday.compute_intraday_features(
                state["fund_series"].get(code), now, CONFIG
            )
        state["features"] = features
        for _key, entry in state.get("candidates", {}).items():
            code = _key.split(":", 1)[1] if ":" in _key else ""
            if code in features:
                entry.setdefault("latest_metrics", {})["features"] = features[code]

        state["pool_entries"] = intraday.select_radar_pool(
            state.get("pool_entries"), buy_records, now, _radar_pool_cfg()
        )
        # P1-R2：选池后统一回填 features，protected 与新成员都保留
        for code, entry in (state.get("pool_entries") or {}).items():
            if code in features:
                entry.setdefault("latest_metrics", {})["features"] = features[code]

        # P1-R5：auto_fallback 在 save_state 之前赋值并持久化
        bulk_validation = bulk_meta.get("validation") if bulk_meta else None
        fallback_decision = pipeline_mod.evaluate_auto_fallback(audit, bulk_validation, coverage_ratio, deadline_met)
        auto_fallback = fallback_decision["reasons"] if fallback_decision["should_fallback"] else None
        if auto_fallback:
            state["auto_fallback"] = "strict_auto_fallback"

        intraday.save_state(state)

        if dry_run or not buy_fresh.empty:
            buy_map = _update_notified_map(buy_map, buy_fresh, now)
            save_notified_map(today, DIRECTION_BUY, buy_map)
        if dry_run or email_sent:
            sell_map = _update_notified_map(sell_map, sell_fresh, now)
            save_notified_map(today, DIRECTION_SELL, sell_map)
    else:
        # readonly：基于本轮结果构造自洽列表，不持久化
        state["candidates"] = intraday.merge_candidate_state({"candidates": {}}, buy_records, sell_records, batch_meta)

    processing_latency_ms = int((time.monotonic() - started_monotonic) * 1000) - truth_latency_ms - bulk_latency_ms

    # P0-R1：M5 单轮准入由完整条件计算，不得复制 truth 标记
    round_valid = pipeline_mod.compute_round_valid(truth, bulk_meta, audit, deadline_met, truth.get("universe_verified"))
    if audit is not None:
        audit["valid_for_admission"] = round_valid

    buy_current, sell_current, buy_today, sell_today = intraday.current_candidate_lists(state)

    result = {
        "schema_version": 2,
        "status": status,
        "reason": _status_reason(status, coverage_ratio, deadline_met),
        "trade_date": today.isoformat(),
        "session": session_label,
        "round_valid": round_valid,
        "sentinel_label": sentinel_label,
        "latencies": {"truth_ms": truth_latency_ms, "bulk_ms": bulk_latency_ms, "processing_ms": processing_latency_ms},
        "batch_id": batch_id,
        "owner_id": owner_id,
        "execution_mode": execution_mode,
        "pipeline_mode": effective_pipeline_mode,
        "requested_pipeline_mode": requested_pipeline_mode,
        "now": now.isoformat(),
        "deadline_met": deadline_met,
        "quality": quality,
        "universe": {"source": truth.get("source", "none"), "count": len(requested),
                     "stale": bool(source_status.get("is_stale") or source_status.get("codes_stale_date")),
                     "verified": bool(truth.get("universe_verified")),
                     "meta": truth.get("universe_meta")},
        "bulk": bulk_meta or {"status": "not_run", "rows": 0, "main_board_rows": 0,
                              "admitted": False, "candidate_count": 0, "latency_ms": None},
        "refine": {
            "status": "complete" if coverage_ratio == 1.0 else "partial",
            "requested_count": len(requested),
            "received_count": len(received),
            "missing_codes": missing[:100],
            "missing_count": len(missing),
            "coverage_ratio": coverage_ratio,
            "latency_ms": int(elapsed_seconds * 1000),
            "invalid_rows": len(rejected_rows),
            "rejected_rows": dict(list(rejected_rows.items())[:100]),
        },
        "audit": audit or {"kind": "shadow_truth", "truth_buy_count": 0, "truth_sell_count": 0,
                           "false_negative_buy": [], "false_negative_sell": [],
                           "valid_for_admission": False},
        "thresholds": {"buy": buy_threshold, "sell": sell_threshold},
        "source_status": source_status,
        "scanned": len(received),
        "buy_candidates": int(len(buy_cands)),
        "sell_candidates": int(len(sell_cands)),
        "buy_fresh_count": int(len(buy_fresh)),
        "sell_fresh_count": int(len(sell_fresh)),
        "email_sent": bool(email_sent),
        "email_error": email_error,
        "buy_triggered": buy_fresh.to_dict("records"),
        "sell_triggered": sell_fresh.to_dict("records"),
        "buy_candidates_current": buy_current,
        "sell_candidates_current": sell_current,
        "buy_candidates_today": buy_today,
        "sell_candidates_today": sell_today,
        "features": state.get("features", {}),
        "auto_fallback": auto_fallback,
    }
    if execution_mode == "official":
        write_report(result)
        append_history(result)
        if not is_replay:
            _record_m5_audit(result, truth, elapsed_seconds)
    elif execution_mode == "shadow":
        write_shadow_report(result)
    return result


def finalize_principal_capital_session(
    session: str,
    now: Optional[datetime] = None,
    execution_mode: Optional[str] = None,
    owner_id: Optional[str] = None,
    manual_retry: bool = False,
    retry_operator: Optional[str] = None,
) -> dict:
    """午间/收盘摘要 finalizer（P0-R3）。

    - 状态机：not_attempted / pending / sent / explicit_failed / delivery_unknown。
    - pending+attempt_id 重启后解释为 delivery_unknown，禁止自动重发。
    - explicit_failed 仅允许显式 manual_retry 重试（带操作者审计）。
    """
    now = now or datetime.now(BEIJING_TZ)
    now = intraday._ensure_aware(now)
    execution_mode = resolve_execution_mode(execution_mode)
    if session not in ("am", "pm"):
        raise ValueError(f"非法 session: {session!r}")
    state = intraday.load_state(trade_date=now.date().isoformat(), now=now)
    latest = read_report()

    result = {
        "status": "skipped", "session": session, "now": now.isoformat(),
        "reason": "", "skipped_reason": None, "email_sent": False, "email_error": None,
    }

    if execution_mode == "official":
        owner_id = owner_id or CONFIG["official_owner"]
        if not owner_id or (state.get("owner_id") or "") != owner_id:
            result.update({"status": "owner_conflict", "reason": "owner_mismatch"})
            return result
        expires = intraday._parse_dt(state.get("owner_lease_expires_at"))
        if expires is None or expires <= now:
            result.update({"status": "owner_conflict", "reason": "owner_lease_expired"})
            return result

    decision = intraday.should_finalize_session(state, session, latest, now, CONFIG)
    result["reason"] = decision["reason"]
    result["skipped_reason"] = decision.get("skipped_reason")

    # P0-R3：explicit_failed 仅允许显式手工重试
    allow_send = decision["should_send"]
    if decision["reason"] == "explicit_failed" and manual_retry:
        allow_send = True
        result["manual_retry"] = True
        result["retry_operator"] = retry_operator or "manual"

    if not allow_send:
        if execution_mode == "official":
            state = intraday.release_owner(state)
            intraday.save_state(state)
        return result

    _buy_current, _sell_current, buy_today, _sell_today = intraday.current_candidate_lists(state)
    subject, text, html_content = build_summary_payload(buy_today, session, now)
    if execution_mode != "official":
        result.update({"status": "constructed", "subject": subject, "text": text})
        return result

    previous_entry = ((state.get("summary_state") or {}).get(session) or {})
    attempt_id = uuid.uuid4().hex
    state = intraday.mark_summary_pending(state, session, now.isoformat(), attempt_id)
    if previous_entry.get("attempt_id"):
        # 记录原 attempt_id 便于审计（手工重试场景）
        entry = dict(state["summary_state"].get(session) or {})
        entry["previous_attempt_id"] = previous_entry.get("attempt_id")
        entry["retry_operator"] = result.get("retry_operator")
        state["summary_state"][session] = entry
    intraday.save_state(state)
    try:
        ok, error = send_email(subject, text, html_content, get_smtp_config())
    except Exception as exc:  # noqa: BLE001 结果不明 -> delivery_unknown，禁止自动重发
        state = intraday.mark_summary_delivery_unknown(state, session, now.isoformat())
        state = intraday.release_owner(state)
        intraday.save_state(state)
        result.update({"status": "delivery_unknown", "email_error": f"{type(exc).__name__}: {exc}"})
        return result

    result["email_sent"] = bool(ok)
    result["email_error"] = error
    if ok:
        state = intraday.mark_summary_sent(state, session, now.isoformat())
        result["status"] = "sent"
    else:
        state = intraday.mark_summary_explicit_failed(state, session, now.isoformat())
        result["status"] = "send_failed"
    state = intraday.release_owner(state)
    intraday.save_state(state)
    return result


def backtest_principal_capital(
    start_date: date,
    end_date: date,
    buy_threshold: float = 50.0,
    sell_threshold: float = 30.0,
    hold_days: int = 5,
) -> dict:
    """回测接口预留。"""
    del start_date, end_date, buy_threshold, sell_threshold, hold_days
    raise NotImplementedError("回测接口预留，待数据源就绪后实现")
