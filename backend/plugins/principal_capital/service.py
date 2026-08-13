"""主力资金双向监控服务（plugin 独立版本）。

对外暴露的核心入口：
  - run_principal_capital_scan(...)  ← 给 CLI / Cron / Router 调用
  - read_report() / read_history()    ← 给 Router 读历史
"""
import html
import json
import logging
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

import pandas as pd

# === 唯一对主项目的只读依赖：交易时段判断函数 ===
# 此函数仅返回 dict，不产生副作用，可安全复用
from backend.api.router_system import trading_session_status

# === plugin 内部依赖 ===
from .config import (
    CONFIG,
    DATA_DIR,
    HISTORY_FILE,
    REPORT_DIR,
    REPORT_FILE,
    SNAPSHOT_RAW_BASE,
    SOURCE_HEALTH_FILE,
)
from .notifier import get_smtp_config, send_email
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
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"date": today.isoformat(), "notified": notified_map}
    _notified_file(today, direction).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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
    result = result[result["code"].map(_is_main_board)]
    result = result[~result["code"].map(_is_excluded_market)]
    if exclude_star:
        result = result[~result["code"].map(_is_star_market)]
    result = result[~result["name"].map(_is_st_name)]
    result = result[result["main_inflow_ratio"].notna()]
    amount_mask = result["total_amount"].isna() | (result["total_amount"] >= float(min_amount))
    result = result[amount_mask]
    return result.reset_index(drop=True)


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
) -> Tuple[str, str, str]:
    buy_count = len(buy_fresh)
    sell_count = len(sell_fresh)
    danger_count = sum(1 for _, row in sell_fresh.iterrows() if row.get("severity") == "danger")
    stamp = now.strftime("%Y-%m-%d %H:%M")
    if buy_count and sell_count:
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
    text = text_lines(buy_fresh, "买入区") + "\n" + text_lines(sell_fresh, "卖出区", include_severity=True)
    if notices:
        text += "\n【数据滞后提示】\n" + "\n".join(f"· {n}" for n in notices) + "\n"
    text += f"\n数据源: {source_status.get('active_source', '--')} / stale={source_status.get('is_stale', False)}\n"

    def html_rows(df: pd.DataFrame) -> str:
        if df.empty:
            return '<tr><td colspan="8">暂无</td></tr>'
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
    html_content = f"""<!DOCTYPE html>
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:20px;color:#111827">
<h2 style="margin:0 0 12px">主力资金双向监控</h2>
{stale_banner}
<h3 style="color:#b91c1c">买入区</h3>
<table style="width:100%;border-collapse:collapse;margin-bottom:16px" border="1" cellspacing="0" cellpadding="8">
<tr style="background:#fef2f2"><th>代码</th><th>名称</th><th>时间</th><th>占比</th><th>主力净流入</th><th>成交额</th><th>涨幅</th><th>级别</th></tr>
{html_rows(buy_fresh)}
</table>
<h3 style="color:#1d4ed8">卖出区</h3>
<table style="width:100%;border-collapse:collapse" border="1" cellspacing="0" cellpadding="8">
<tr style="background:#eff6ff"><th>代码</th><th>名称</th><th>时间</th><th>占比</th><th>主力净流入</th><th>成交额</th><th>涨幅</th><th>级别</th></tr>
{html_rows(sell_fresh)}
</table>
<p style="margin-top:16px;color:#6b7280">数据源: {html.escape(str(source_status.get('active_source', '--')))} | stale={html.escape(str(source_status.get('is_stale', False)))}</p>
</body></html>"""
    return subject, text, html_content


# ---- 报告落盘 ----

def write_report(payload: dict) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


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
    """从 data-snapshots 分支拉取 JSON 快照，失败返回 None。"""
    import requests  # 延迟导入，避免 CLI/离线路径强依赖
    url = f"{SNAPSHOT_RAW_BASE}/{directory}/{filename}"
    try:
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001 网络/解析异常都视为回退失败
        logger.info("主力资金远程快照拉取失败(%s): %s", filename, exc)
        return None


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


def read_report_resilient() -> dict:
    """读最新报告：本地完成态优先，否则返回远程快照的真实状态。"""
    local = _normalize_report_payload(read_report())
    if local.get("status") == "completed":
        return local
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
    HISTORY_FILE.write_text(
        json.dumps({"records": records[-max_records:]}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


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
                  sell_threshold: float, source_status: Optional[dict] = None) -> dict:
    return {
        "status": status,
        "reason": reason,
        "now": now.isoformat(),
        "thresholds": {"buy": buy_threshold, "sell": sell_threshold},
        "source_status": source_status or {"active_source": "none"},
        "scanned": 0,
        "buy_candidates": 0, "sell_candidates": 0,
        "buy_fresh_count": 0, "sell_fresh_count": 0,
        "email_sent": False, "email_error": None,
        "buy_triggered": [], "sell_triggered": [],
    }


def run_principal_capital_scan(
    now: Optional[datetime] = None,
    buy_threshold: float = 50.0,
    sell_threshold: float = 30.0,
    exclude_star: bool = True,
    sell_cooldown_minutes: int = 60,
    enable_verify: bool = False,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    """执行主力资金双向扫描。"""
    now = now or datetime.now(BEIJING_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=BEIJING_TZ)
    session = trading_session_status(now)
    if not force and not session.get("is_trading_hours"):
        result = _empty_result(
            "skipped", session.get("market_status_text", "非交易时段"),
            now, buy_threshold, sell_threshold,
        )
        write_report(result)
        return result

    today = now.date()
    cleanup_old_notified(today)
    buy_map = load_notified_map(today, DIRECTION_BUY)
    sell_map = load_notified_map(today, DIRECTION_SELL)

    df, source_status = fetch_market_fund_flow_resilient(enable_verify=enable_verify)
    if df.empty:
        result = _empty_result("no_data", "", now, buy_threshold, sell_threshold, source_status)
        write_report(result)
        return result

    buy_cands = filter_buy_candidates(df, buy_threshold=buy_threshold, exclude_star=exclude_star)
    sell_cands = filter_sell_candidates(df, sell_threshold=sell_threshold, exclude_star=exclude_star)
    buy_fresh = _fresh_rows(buy_cands, DIRECTION_BUY, now, buy_map, CONFIG["buy_dedup_minutes"])
    sell_fresh = _fresh_rows(sell_cands, DIRECTION_SELL, now, sell_map, sell_cooldown_minutes)

    email_sent = False
    email_error = None
    if (not buy_fresh.empty or not sell_fresh.empty) and not dry_run:
        subject, text, html_content = build_email_payload(buy_fresh, sell_fresh, now, source_status)
        email_sent, email_error = send_email(subject, text, html_content, get_smtp_config())

    if dry_run or email_sent:
        buy_map = _update_notified_map(buy_map, buy_fresh, now)
        sell_map = _update_notified_map(sell_map, sell_fresh, now)
        save_notified_map(today, DIRECTION_BUY, buy_map)
        save_notified_map(today, DIRECTION_SELL, sell_map)

    result = {
        "status": "completed",
        "reason": "",
        "now": now.isoformat(),
        "thresholds": {"buy": buy_threshold, "sell": sell_threshold},
        "source_status": source_status,
        "scanned": int(len(df)),
        "buy_candidates": int(len(buy_cands)),
        "sell_candidates": int(len(sell_cands)),
        "buy_fresh_count": int(len(buy_fresh)),
        "sell_fresh_count": int(len(sell_fresh)),
        "email_sent": bool(email_sent),
        "email_error": email_error,
        "buy_triggered": buy_fresh.to_dict("records"),
        "sell_triggered": sell_fresh.to_dict("records"),
    }
    write_report(result)
    append_history(result)
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
