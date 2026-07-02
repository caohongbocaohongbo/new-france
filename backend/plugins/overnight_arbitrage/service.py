"""尾盘隔夜套利插件策略服务。"""
import html
import json
import logging
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from . import notifier
from .config import BEIJING_TZ, CONFIG, HISTORY_FILE, PROJECT_DIR, REPORT_FILE, SNAPSHOT_RAW_BASE

logger = logging.getLogger(__name__)


def _json_safe(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _float(value, default: Optional[float] = None) -> Optional[float]:
    if value is None or value == "" or value == "-":
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(result):
        return default
    return result


def _int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _round_or_none(value, digits: int = 2) -> Optional[float]:
    number = _float(value)
    return None if number is None else round(number, digits)


def _intraday_pullback_pct(price, high) -> Optional[float]:
    latest = _float(price)
    high_price = _float(high)
    if latest is None or high_price is None or high_price <= 0:
        return None
    if latest >= high_price:
        return 0.0
    return round((latest - high_price) / high_price * 100, 2)


def _is_excluded_market(code: str) -> bool:
    return code.startswith(("300", "301"))


def _stock_code(value) -> str:
    text = str(value or "").strip()
    return text.zfill(6) if text else ""


def _append_quote_source(quotes: pd.DataFrame, source_name: str) -> pd.DataFrame:
    if quotes is None:
        return pd.DataFrame()
    result = quotes.copy()
    if not result.empty:
        result["数据源"] = source_name
    return result


def _empty_source_status(source_name: str, status: str, count: int = 0, error: Optional[str] = None) -> dict:
    result = {"source": source_name, "status": status, "count": int(count or 0)}
    if error:
        result["error"] = str(error)
    return result


def _normalize_error_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _detect_quote_channel_issue(quote_source_status: Optional[List[dict]]) -> dict:
    statuses = quote_source_status or []
    if not statuses:
        return {"status": "unknown", "kind": "unknown", "scope": "unknown"}

    quote_statuses = [item for item in statuses if item.get("source") != "eastmoney_zt_pool_quote_fallback"]
    if not quote_statuses:
        return {"status": "unknown", "kind": "unknown", "scope": "unknown"}

    errors = [item for item in quote_statuses if item.get("status") == "error"]
    if not errors:
        return {"status": "ok", "kind": "none", "scope": "sources"}

    error_text = " | ".join(_normalize_error_text(item.get("error")) for item in errors)
    dns_markers = ("failed to resolve", "nameresolutionerror", "nodename nor servname provided")
    if error_text and all(marker in error_text for marker in ("httpsconnectionpool",)):
        if any(marker in error_text for marker in dns_markers):
            return {
                "status": "error",
                "kind": "dns_resolution_failed",
                "scope": "runtime_environment",
                "note": "运行环境 DNS 解析失败，主备行情源都未连通。",
            }

    if len(errors) == len(quote_statuses):
        return {
            "status": "error",
            "kind": "all_quote_sources_failed",
            "scope": "sources",
            "note": "主备行情源都失败，但未识别为统一的运行环境 DNS 故障。",
        }

    return {"status": "degraded", "kind": "partial_source_failure", "scope": "sources"}


def _build_empty_reason(total: int, quote_source_status: Optional[List[dict]]) -> str:
    if total > 0:
        return ""
    channel = _detect_quote_channel_issue(quote_source_status)
    if channel.get("kind") == "dns_resolution_failed":
        return "运行环境 DNS/外网通道异常，东方财富与新浪主备行情源均无法解析，未扫描到可判断股票。"
    if channel.get("kind") == "all_quote_sources_failed":
        return "全市场行情主备源均失败，且涨停池兜底为空，未扫描到可判断股票。"
    return "全市场行情主备源均不可用或返回空数据，未扫描到可判断股票。"


def _reject_reason(row: dict) -> Optional[str]:
    code = str(row.get("代码", "")).zfill(6)
    name = str(row.get("名称", "")).upper()
    if _is_excluded_market(code):
        return "创业板已排除"
    if "ST" in name or "退" in name:
        return "ST或退市风险已排除"
    price = _float(row.get("最新价"), 0) or 0
    if price <= 0:
        return "停牌或无有效价格"
    amount = _float(row.get("成交额"), 0) or 0
    if amount < float(CONFIG["min_amount_yuan"]):
        return "成交额不足"
    turnover = _float(row.get("换手率"), 0) or 0
    if turnover < 2:
        return "换手率不足"
    change_pct = _float(row.get("涨跌幅"), 0) or 0
    if change_pct < 5.5:
        return "涨幅不足"
    return None


def _seal_time_score(seal_time: int) -> float:
    if seal_time <= 0:
        return 0
    if seal_time <= 143000:
        return 12
    if seal_time <= 144300:
        return 10
    if seal_time <= 145000:
        return 7
    return 3


def _minute_score(strength: dict) -> float:
    if not strength:
        return 0
    change = _float(strength.get("last_15m_change_pct"), 0) or 0
    close_position = _float(strength.get("last_close_position"), 0) or 0
    return _clip(change * 4, -8, 10) + _clip((close_position - 0.5) * 16, -5, 8)


def _build_zt_map(zt_pool: Optional[pd.DataFrame]) -> dict:
    if zt_pool is None or zt_pool.empty:
        return {}
    result = {}
    for _, item in zt_pool.iterrows():
        code = str(item.get("代码", "")).zfill(6)
        result[code] = {
            "seal_time": _int(item.get("封板时间")),
            "break_count": _int(item.get("炸板次数")),
            "consecutive": _int(item.get("连板数")),
        }
    return result


def _decision_item(row: dict, zt_info: dict, minute: dict) -> dict:
    code = str(row.get("代码", "")).zfill(6)
    current_price = _float(row.get("最新价"))
    change_pct = _float(row.get("涨跌幅"), 0) or 0
    amount = _float(row.get("成交额"), 0) or 0
    turnover = _float(row.get("换手率"), 0) or 0
    volume_ratio = _float(row.get("量比"), 0) or 0
    float_mcap = _float(row.get("流通市值"), 0)
    seal_time = int(zt_info.get("seal_time") or 0)
    break_count = int(zt_info.get("break_count") or 0)
    consecutive = int(zt_info.get("consecutive") or 0)

    score = 0.0
    score += _clip((change_pct - 5.5) * 4.2, 0, 22)
    score += _clip(amount / 100_000_000 * 2.2, 0, 18)
    score += _clip(turnover * 2.0, 0, 16)
    score += _clip(volume_ratio * 4.0, 0, 14)
    score += _seal_time_score(seal_time)
    score += _minute_score(minute)
    score -= break_count * 9

    if consecutive >= 2:
        score += 4
    if float_mcap and float_mcap > 20_000_000_000:
        score -= 4

    score = round(_clip(score, 0, 100), 2)
    thresholds = CONFIG["score_thresholds"]
    if score >= float(thresholds["buy"]):
        action = "BUY"
    elif (
        score >= float(thresholds["watch"])
        or (
            change_pct >= float(thresholds["watch_min_change_pct"])
            and amount >= float(thresholds["watch_min_amount_yuan"])
        )
    ):
        action = "WATCH"
    else:
        action = "PASS"

    reasons = []
    risks = []
    if change_pct >= 9:
        reasons.append("涨幅接近涨停")
    elif change_pct >= 7:
        reasons.append("尾盘涨幅强")
    if amount >= 300_000_000:
        reasons.append("成交额充足")
    if turnover >= 5:
        reasons.append("换手充分")
    if volume_ratio >= 2:
        reasons.append("量比放大")
    if seal_time:
        reasons.append("封板信息有效")
    if minute:
        reasons.append("尾盘承接较强")
    else:
        risks.append("5分钟K线缺失")
    if break_count:
        risks.append(f"炸板{break_count}次")
    if not seal_time:
        risks.append("未进入涨停池或封板信息缺失")
    if action == "PASS":
        risks.append("综合分未达买入阈值")

    return {
        "code": code,
        "name": str(row.get("名称", "")),
        "action": action,
        "decision_score": score,
        "current_price": current_price,
        "pe": _round_or_none(row.get("市盈率")),
        "intraday_pullback_pct": _intraday_pullback_pct(current_price, row.get("最高价")),
        "change_pct": round(change_pct, 2),
        "turnover": round(turnover, 2),
        "volume_ratio": round(volume_ratio, 2),
        "amount": amount,
        "float_mcap": float_mcap,
        "seal_time": seal_time,
        "break_count": break_count,
        "consecutive": consecutive,
        "minute_strength": minute or {},
        "reasons": reasons[:6],
        "risks": risks[:5],
        "valid_until": "14:55",
    }


def build_overnight_decision(
    quotes: pd.DataFrame,
    *,
    zt_pool: Optional[pd.DataFrame] = None,
    minute_strength: Optional[Dict[str, dict]] = None,
    target_date: Optional[date] = None,
    generated_at: Optional[str] = None,
    limit: int = 20,
    quote_source_status: Optional[List[dict]] = None,
    errors: Optional[List[str]] = None,
) -> dict:
    """根据实时行情和可选分时增强源生成 14:43 买入决策。"""
    target_date = target_date or datetime.now(BEIJING_TZ).date()
    generated_at = generated_at or datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    minute_strength = minute_strength or {}
    zt_map = _build_zt_map(zt_pool)

    rejected = []
    candidates = []
    total = 0 if quotes is None else len(quotes)
    if quotes is not None and not quotes.empty:
        for _, series in quotes.iterrows():
            row = series.to_dict()
            code = str(row.get("代码", "")).zfill(6)
            reason = _reject_reason(row)
            if reason:
                rejected.append({"code": code, "name": str(row.get("名称", "")), "reason": reason})
                continue
            item = _decision_item(row, zt_map.get(code, {}), minute_strength.get(code, {}))
            if item["action"] != "PASS":
                candidates.append(item)
            else:
                rejected.append({"code": code, "name": item["name"], "reason": "综合分未达买入阈值"})

    candidates.sort(key=lambda item: item["decision_score"], reverse=True)
    results = candidates[:limit]
    errors = errors or []
    has_quotes = total > 0
    status = "completed" if not errors else "completed_with_errors"
    if not has_quotes:
        status = "data_unavailable"
    normalized_quote_status = quote_source_status or [_empty_source_status("eastmoney_all_a", "ok" if total else "empty", total)]
    return {
        "status": status,
        "strategy": "overnight_arbitrage",
        "date": target_date.strftime("%Y-%m-%d"),
        "generated_at": generated_at,
        "decision_time": "14:43",
        "valid_window": "14:43-14:55",
        "buy_count": sum(1 for item in results if item["action"] == "BUY"),
        "watch_count": sum(1 for item in results if item["action"] == "WATCH"),
        "total_candidates": len(results),
        "total_scanned": total,
        "results": results,
        "rejected": rejected[:50],
        "source_status": {
            "quotes": normalized_quote_status,
            "channel": _detect_quote_channel_issue(normalized_quote_status),
            "eastmoney_zt_pool": {"status": "ok" if zt_map else "optional_missing", "count": len(zt_map)},
            "yahoo_5m": {"status": "ok" if minute_strength else "optional_missing", "count": len(minute_strength)},
        },
        "errors": errors,
        "empty_reason": _build_empty_reason(total, normalized_quote_status),
        "trade_note": "当日14:43生成买入决策；次日09:30-09:35仅用于卖出复盘和参数校准。",
    }


def write_overnight_report(payload: dict, report_file: Path = REPORT_FILE) -> None:
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_overnight_report(report_file: Path = REPORT_FILE) -> dict:
    if not report_file.exists():
        return {
            "status": "empty",
            "strategy": "overnight_arbitrage",
            "results": [],
            "message": "暂无尾盘隔夜套利决策，请先运行任务",
        }
    return json.loads(report_file.read_text(encoding="utf-8"))


def _empty_history() -> dict:
    return {
        "status": "empty",
        "strategy": "overnight_arbitrage_history",
        "updated_at": None,
        "total_stocks": 0,
        "total_recommendations": 0,
        "records": [],
    }


def read_overnight_history(history_file: Path = HISTORY_FILE) -> dict:
    if not history_file.exists():
        return _empty_history()
    return json.loads(history_file.read_text(encoding="utf-8"))


# ---- data-snapshots 远程回退（Render 自身取不到东财，读快照分支保证与定时任务同源）----

_OA_OK_STATUSES = {"completed", "completed_with_errors"}


def _fetch_snapshot_json(filename: str) -> Optional[dict]:
    """从 data-snapshots 分支的 GitHub raw 拉取 reports/<filename>。失败返回 None。"""
    import requests  # 延迟导入
    url = f"{SNAPSHOT_RAW_BASE}/reports/{filename}"
    try:
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.info("隔夜套利远程快照拉取失败(%s): %s", filename, exc)
        return None


def read_overnight_report_resilient() -> dict:
    """读最新决策：本地为完成态直接返回，否则回退到 data-snapshots 快照。"""
    local = read_overnight_report()
    if local.get("status") in _OA_OK_STATUSES:
        return local
    remote = _fetch_snapshot_json("overnight_arbitrage_latest.json")
    if remote and remote.get("status") in _OA_OK_STATUSES:
        remote["_source"] = "snapshot"
        return remote
    return local


def read_overnight_history_resilient() -> dict:
    """读历史：本地有记录直接返回，否则回退到 data-snapshots 快照。"""
    local = read_overnight_history()
    if local.get("records"):
        return local
    remote = _fetch_snapshot_json("overnight_arbitrage_history.json")
    if remote and remote.get("records"):
        return remote
    return local


def _history_values(recommendations: List[dict], key: str) -> List[float]:
    values = []
    for item in recommendations:
        value = _float(item.get(key))
        if value is not None:
            values.append(round(value, 3 if key == "price" else 2))
    return values


def _history_metric(values: List[float]) -> dict:
    if not values:
        return {"count": 0, "avg": None, "min": None, "max": None}
    return {
        "count": len(values),
        "avg": round(sum(values) / len(values), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
    }


def _rebuild_history_record(code: str, name: str, recommendations: List[dict]) -> dict:
    recommendations = sorted(recommendations, key=lambda item: (str(item.get("date", "")), str(item.get("generated_at", ""))))
    price_values = _history_values(recommendations, "price")
    pe_values = _history_values(recommendations, "pe")
    pullback_values = _history_values(recommendations, "pullback_pct")
    price_metric = _history_metric(price_values)
    pe_metric = _history_metric(pe_values)
    pullback_metric = _history_metric(pullback_values)
    last = recommendations[-1] if recommendations else {}
    return {
        "code": code,
        "name": name,
        "recommendation_count": len(recommendations),
        "first_recommended_at": recommendations[0].get("generated_at") if recommendations else None,
        "last_recommended_at": last.get("generated_at"),
        "last_action": last.get("action"),
        "price_pushes": price_values,
        "pe_values": pe_values,
        "pullback_values": pullback_values,
        "metrics": {
            "price_count": price_metric["count"],
            "price_avg": price_metric["avg"],
            "price_min": price_metric["min"],
            "price_max": price_metric["max"],
            "pe_count": pe_metric["count"],
            "pe_avg": pe_metric["avg"],
            "pe_min": pe_metric["min"],
            "pe_max": pe_metric["max"],
            "pullback_count": pullback_metric["count"],
            "pullback_avg": pullback_metric["avg"],
            "pullback_min": pullback_metric["min"],
            "pullback_max": pullback_metric["max"],
        },
        "recommendations": recommendations,
    }


def update_overnight_history(decision: dict, history_file: Path = HISTORY_FILE) -> dict:
    """按股票+交易日去重保存 BUY/WATCH 历史，并重算跨日统计。"""
    history = read_overnight_history(history_file)
    trade_date = str(decision.get("date") or "")
    generated_at = str(decision.get("generated_at") or "")
    if not trade_date and len(generated_at) >= 10:
        trade_date = generated_at[:10]
    if not trade_date:
        trade_date = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

    by_code: Dict[str, dict] = {}
    for record in history.get("records") or []:
        code = _stock_code(record.get("code"))
        if not code:
            continue
        by_date = {
            str(item.get("date")): item
            for item in record.get("recommendations") or []
            if item.get("date")
        }
        by_code[code] = {
            "name": str(record.get("name") or ""),
            "by_date": by_date,
        }

    for item in decision.get("results") or []:
        action = item.get("action")
        if action not in {"BUY", "WATCH"}:
            continue
        code = _stock_code(item.get("code"))
        if not code:
            continue
        holder = by_code.setdefault(code, {"name": str(item.get("name") or ""), "by_date": {}})
        holder["name"] = str(item.get("name") or holder.get("name") or "")
        existing = holder["by_date"].get(trade_date)
        if existing and str(existing.get("generated_at") or "") > generated_at:
            continue
        holder["by_date"][trade_date] = {
            "date": trade_date,
            "generated_at": generated_at,
            "action": action,
            "price": _round_or_none(item.get("current_price"), 3),
            "pe": _round_or_none(item.get("pe")),
            "pullback_pct": _round_or_none(item.get("intraday_pullback_pct")),
            "decision_score": _round_or_none(item.get("decision_score")),
        }

    records = []
    for code, holder in by_code.items():
        recommendations = [item for _, item in sorted(holder.get("by_date", {}).items())]
        if recommendations:
            records.append(_rebuild_history_record(code, holder.get("name") or "", recommendations))

    records.sort(
        key=lambda item: (
            -item["recommendation_count"],
            str(item.get("last_recommended_at") or ""),
            item["code"],
        )
    )
    payload = {
        "status": "completed",
        "strategy": "overnight_arbitrage_history",
        "updated_at": generated_at or datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "total_stocks": len(records),
        "total_recommendations": sum(item["recommendation_count"] for item in records),
        "records": records,
    }
    history_file.parent.mkdir(parents=True, exist_ok=True)
    history_file.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _attach_history_summary(decision: dict, history: dict) -> None:
    records = {_stock_code(item.get("code")): item for item in history.get("records") or []}
    for item in decision.get("results") or []:
        record = records.get(_stock_code(item.get("code")))
        if not record:
            continue
        item["history"] = {
            "recommendation_count": record.get("recommendation_count", 0),
            "price_pushes": record.get("price_pushes", []),
            "pe_values": record.get("pe_values", []),
            "pullback_values": record.get("pullback_values", []),
            "metrics": record.get("metrics", {}),
        }
    decision["history_summary"] = {
        "status": history.get("status"),
        "history_file": "reports/overnight_arbitrage_history.json",
        "total_stocks": history.get("total_stocks", 0),
        "total_recommendations": history.get("total_recommendations", 0),
        "updated_at": history.get("updated_at"),
    }


def _eastmoney_all_a_snapshot() -> pd.DataFrame:
    """拉取沪深全 A 实时快照，供尾盘套利粗筛。"""
    import requests

    url = "https://push2.eastmoney.com/api/qt/clist/get"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/center/gridlist.html"}
    fields = "f2,f3,f5,f6,f8,f9,f10,f12,f14,f15,f20,f21"
    rows = []
    for fs in ("m:1+t:2,m:1+t:23", "m:0+t:6,m:0+t:80"):
        for page in range(1, 8):
            params = {
                "pn": page,
                "pz": 200,
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fid": "f3",
                "fs": fs,
                "fields": fields,
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            }
            resp = requests.get(url, params=params, headers=headers, timeout=12)
            resp.raise_for_status()
            diff = resp.json().get("data", {}).get("diff") or []
            if not diff:
                break
            for item in diff:
                rows.append({
                    "代码": str(item.get("f12", "")).zfill(6),
                    "名称": str(item.get("f14", "")),
                    "最新价": _float(item.get("f2")),
                    "涨跌幅": _float(item.get("f3")),
                    "最高价": _float(item.get("f15")),
                    "成交量": _float(item.get("f5")),
                    "成交额": _float(item.get("f6")),
                    "换手率": _float(item.get("f8")),
                    "市盈率": _float(item.get("f9")),
                    "量比": _float(item.get("f10")),
                    "总市值": _float(item.get("f20")),
                    "流通市值": _float(item.get("f21")),
                })
    return pd.DataFrame(rows)


def _sina_all_a_snapshot(max_pages: int = 80) -> pd.DataFrame:
    """新浪财经全A兜底源；过滤创业板，只保留沪深可交易A股字段。"""
    import requests

    url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
    rows = []
    seen = set()
    for page in range(1, max_pages + 1):
        params = {
            "page": page,
            "num": 80,
            "sort": "changepercent",
            "asc": 0,
            "node": "hs_a",
            "symbol": "",
            "_s_r_a": "page",
        }
        resp = requests.get(url, params=params, headers=headers, timeout=12)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        for item in data:
            code = str(item.get("code") or "").zfill(6)
            symbol = str(item.get("symbol") or "")
            if not code or code in seen or _is_excluded_market(code):
                continue
            if not symbol.startswith(("sh", "sz", "bj")):
                continue
            latest = _float(item.get("trade"))
            amount = _float(item.get("amount"))
            if latest is None or latest <= 0:
                continue
            rows.append({
                "代码": code,
                "名称": str(item.get("name") or ""),
                "最新价": latest,
                "涨跌幅": _float(item.get("changepercent")),
                "最高价": _float(item.get("high")),
                "成交量": _float(item.get("volume")),
                "成交额": amount,
                "换手率": _float(item.get("turnoverratio")),
                "量比": None,
                "市盈率": _float(item.get("per")),
                "总市值": (_float(item.get("mktcap")) or 0) * 10_000,
                "流通市值": (_float(item.get("nmc")) or 0) * 10_000,
            })
            seen.add(code)
        if len(data) < 80:
            break
    return pd.DataFrame(rows)


def _zt_pool_quote_fallback(zt_pool: Optional[pd.DataFrame]) -> pd.DataFrame:
    """全市场源都失败时，用涨停池做窄范围兜底，避免扫描数变成0。"""
    if zt_pool is None or zt_pool.empty:
        return pd.DataFrame()
    rows = []
    for _, item in zt_pool.iterrows():
        code = str(item.get("代码", "")).zfill(6)
        if not code or _is_excluded_market(code):
            continue
        rows.append({
            "代码": code,
            "名称": str(item.get("名称", "")),
            "最新价": _float(item.get("最新价")),
            "涨跌幅": _float(item.get("涨跌幅")),
            "最高价": _float(item.get("最高价")),
            "成交量": _float(item.get("成交量")),
            "成交额": _float(item.get("成交额")),
            "换手率": _float(item.get("换手率")),
            "市盈率": _float(item.get("市盈率")),
            "量比": _float(item.get("量比")),
            "总市值": _float(item.get("总市值")),
            "流通市值": _float(item.get("流通市值")),
        })
    return pd.DataFrame(rows)


def _fetch_quotes_with_fallbacks(
    primary_fetcher: Callable[[], pd.DataFrame],
    zt_pool: Optional[pd.DataFrame] = None,
) -> Tuple[pd.DataFrame, List[dict], List[str]]:
    """按东方财富全市场 -> 新浪全A -> 涨停池窄范围的顺序取行情。"""
    sources = [
        ("eastmoney_all_a", primary_fetcher),
        ("sina_all_a", _sina_all_a_snapshot),
    ]
    statuses: List[dict] = []
    errors: List[str] = []

    for source_name, fetcher in sources:
        try:
            quotes = fetcher()
            count = 0 if quotes is None else len(quotes)
            if count:
                statuses.append(_empty_source_status(source_name, "ok", count))
                return _append_quote_source(quotes, source_name), statuses, errors
            statuses.append(_empty_source_status(source_name, "empty", 0))
            errors.append(f"{source_name} 返回空行情")
        except Exception as exc:
            logger.warning("尾盘套利行情源 %s 失败: %s", source_name, exc)
            statuses.append(_empty_source_status(source_name, "error", 0, str(exc)))
            errors.append(f"{source_name} 失败: {exc}")

    fallback = _zt_pool_quote_fallback(zt_pool)
    fallback_count = len(fallback)
    if fallback_count:
        statuses.append(_empty_source_status("eastmoney_zt_pool_quote_fallback", "ok", fallback_count))
        return _append_quote_source(fallback, "eastmoney_zt_pool_quote_fallback"), statuses, errors

    statuses.append(_empty_source_status("eastmoney_zt_pool_quote_fallback", "empty", 0))
    errors.append("涨停池窄范围兜底为空")
    return pd.DataFrame(), statuses, errors


def _fetch_yahoo_5m_strength(codes: Iterable[str]) -> Dict[str, dict]:
    """对前排候选拉 Yahoo 5 分钟 K 线，失败时返回空映射。"""
    import time
    import requests

    result = {}
    now = int(time.time())
    start = now - 2 * 24 * 3600
    headers = {"User-Agent": "Mozilla/5.0"}
    for code in list(codes)[:30]:
        suffix = ".SS" if str(code).startswith(("6", "9")) else ".SZ"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}{suffix}"
        params = {"period1": start, "period2": now, "interval": "5m", "includePrePost": "false"}
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=8)
            resp.raise_for_status()
            chart = (resp.json().get("chart", {}).get("result") or [None])[0] or {}
            quote = ((chart.get("indicators") or {}).get("quote") or [None])[0] or {}
            closes = [v for v in quote.get("close") or [] if v is not None]
            highs = [v for v in quote.get("high") or [] if v is not None]
            lows = [v for v in quote.get("low") or [] if v is not None]
            if len(closes) < 4 or not highs or not lows:
                continue
            base = closes[-4]
            change = 0.0 if not base else (closes[-1] - base) / base * 100
            high = max(highs[-6:])
            low = min(lows[-6:])
            position = 0.5 if high <= low else (closes[-1] - low) / (high - low)
            result[str(code)] = {
                "last_15m_change_pct": round(change, 3),
                "last_close_position": round(_clip(position, 0, 1), 3),
                "source": "Yahoo 5m",
            }
        except Exception as exc:
            logger.debug("Yahoo 5m %s 失败: %s", code, exc)
    return result


async def run_overnight_arbitrage(
    target_date: Optional[date] = None,
    *,
    quote_fetcher: Optional[Callable[[], pd.DataFrame]] = None,
    zt_fetcher: Optional[Callable[..., Optional[pd.DataFrame]]] = None,
    minute_fetcher: Optional[Callable[[Iterable[str]], Dict[str, dict]]] = None,
    dry_run: bool = False,
) -> dict:
    """执行尾盘隔夜套利任务，并写入独立报告缓存。"""
    target_date = target_date or datetime.now(BEIJING_TZ).date()
    generated_at = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    quote_fetcher = quote_fetcher or _eastmoney_all_a_snapshot
    if zt_fetcher is None:
        from .sources.zt_pool import fetch_zt_pool
        zt_fetcher = fetch_zt_pool
    minute_fetcher = minute_fetcher or _fetch_yahoo_5m_strength

    errors = []
    quote_source_status = []
    quotes = pd.DataFrame()
    zt_pool = pd.DataFrame()
    minute_strength = {}
    try:
        zt_pool = zt_fetcher(target_date)
    except TypeError:
        zt_pool = zt_fetcher()
    except Exception as exc:
        logger.warning("尾盘套利涨停池失败: %s", exc)
        errors.append(f"涨停池失败: {exc}")
    quotes, quote_source_status, quote_errors = _fetch_quotes_with_fallbacks(quote_fetcher, zt_pool=zt_pool)
    errors.extend(quote_errors)

    seed_codes = []
    if quotes is not None and not quotes.empty:
        tmp_decision = build_overnight_decision(
            quotes,
            zt_pool=zt_pool,
            minute_strength={},
            target_date=target_date,
            generated_at=generated_at,
            limit=30,
        )
        seed_codes = [item["code"] for item in tmp_decision.get("results", [])[:30]]
    try:
        minute_strength = minute_fetcher(seed_codes) if seed_codes else {}
    except Exception as exc:
        logger.warning("尾盘套利5分钟K线增强失败: %s", exc)
        errors.append(f"5分钟K线增强失败: {exc}")

    decision = build_overnight_decision(
        quotes,
        zt_pool=zt_pool,
        minute_strength=minute_strength,
        target_date=target_date,
        generated_at=generated_at,
        limit=20,
        quote_source_status=quote_source_status,
        errors=errors,
    )
    if dry_run:
        decision["history_summary"] = {
            "status": "skipped_dry_run",
            "history_file": "reports/overnight_arbitrage_history.json",
            "message": "dry_run 不写入跨日推荐历史，避免测试污染累计统计",
        }
    else:
        try:
            history = update_overnight_history(decision)
            _attach_history_summary(decision, history)
        except Exception as exc:
            logger.warning("尾盘套利历史统计更新失败: %s", exc)
            decision.setdefault("errors", []).append(f"历史统计更新失败: {exc}")
            if decision.get("status") == "completed":
                decision["status"] = "completed_with_errors"
            decision["history_summary"] = {
                "status": "error",
                "history_file": "reports/overnight_arbitrage_history.json",
                "error": str(exc),
            }
    write_overnight_report(decision)
    if not dry_run:
        notify_overnight_decision(decision)
    return decision


def _fmt_value(value, suffix: str = "", digits: int = 2) -> str:
    number = _float(value)
    if number is None:
        return "--"
    return f"{number:.{digits}f}{suffix}"


def _fmt_amount(value) -> str:
    number = _float(value)
    if number is None or number <= 0:
        return "--"
    if number >= 100_000_000:
        return f"{number / 100_000_000:.1f}亿"
    return f"{number / 10_000:.0f}万"


def _fmt_joined(values: Optional[List[str]]) -> str:
    items = [str(item) for item in (values or []) if str(item).strip()]
    return "、".join(items) if items else "--"


def _overnight_candidate_text_lines(title: str, items: List[dict]) -> List[str]:
    lines = [f"【{title}】"]
    if not items:
        return lines + ["无"]
    for item in items:
        lines.append(
            f"{item.get('code')} {item.get('name')} 分数{item.get('decision_score')} "
            f"现价{_fmt_value(item.get('current_price'))} 涨幅{_fmt_value(item.get('change_pct'), '%')} "
            f"回撤{_fmt_value(item.get('intraday_pullback_pct'), '%')} "
            f"原因:{_fmt_joined(item.get('reasons'))} 风险:{_fmt_joined(item.get('risks'))}"
        )
    return lines


def _overnight_quote_status_text(source_status: dict) -> List[str]:
    lines = []
    channel = source_status.get("channel") or {}
    if channel:
        lines.append(
            "通道: "
            f"{channel.get('status', '--')} / {channel.get('kind', '--')} / {channel.get('scope', '--')}"
        )
    for item in source_status.get("quotes") or []:
        line = f"{item.get('source', '--')}: {item.get('status', '--')} count={item.get('count', 0)}"
        if item.get("error"):
            line += f" error={item.get('error')}"
        lines.append(line)
    return lines


def _build_overnight_text_content(decision: dict, buy_items: List[dict], watch_items: List[dict]) -> str:
    lines = [
        "【尾盘隔夜套利 14:43 决策】",
        f"日期: {decision.get('date')} | 有效窗口: {decision.get('valid_window')}",
        f"状态: {decision.get('status', 'completed')} | 扫描: {decision.get('total_scanned', 0)}",
        f"BUY: {decision.get('buy_count', 0)} | WATCH: {decision.get('watch_count', 0)}",
        "说明: 当日尾盘买入决策；次日09:30-09:35仅用于卖出复盘和参数校准。",
        "",
    ]
    lines.extend(_overnight_candidate_text_lines("BUY 候选", buy_items))
    lines.append("")
    lines.extend(_overnight_candidate_text_lines("WATCH 候选", watch_items))

    source_status = decision.get("source_status") or {}
    status_lines = _overnight_quote_status_text(source_status)
    if status_lines or decision.get("errors") or decision.get("empty_reason"):
        lines.append("")
        lines.append("【数据源/通道】")
        lines.extend(status_lines)
        if decision.get("empty_reason"):
            lines.append(str(decision.get("empty_reason")))
        for err in decision.get("errors") or []:
            lines.append(f"- {err}")
    return "\n".join(lines)


def _html_badge(text: str, color: str) -> str:
    return (
        f'<span style="display:inline-block;padding:3px 8px;border-radius:999px;'
        f'background:{color}18;color:{color};font-size:11px;font-weight:700">'
        f"{html.escape(str(text))}</span>"
    )


def _overnight_candidate_rows(items: List[dict]) -> str:
    if not items:
        return (
            '<tr><td colspan="13" style="padding:14px 10px;text-align:center;color:#667085;'
            'background:#F8FAFC">无候选</td></tr>'
        )

    rows = []
    for idx, item in enumerate(items, start=1):
        bg = "#F8FAFC" if idx % 2 else "#FFFFFF"
        action = str(item.get("action") or "--")
        color = "#E74C3C" if action == "BUY" else "#3498DB"
        change = _float(item.get("change_pct"), 0) or 0
        change_color = "#E74C3C" if change >= 0 else "#16A36A"
        pullback = _float(item.get("intraday_pullback_pct"))
        pullback_color = "#16A36A" if pullback is not None and pullback < 0 else "#667085"
        rows.append(f"""
    <tr style="background:{bg}">
      <td style="padding:8px 10px;text-align:center;color:#667085;font-size:12px">{idx}</td>
      <td style="padding:8px 10px;font-family:Menlo,Consolas,monospace;font-size:12px;color:#172033">{html.escape(str(item.get('code') or '--'))}</td>
      <td style="padding:8px 10px;font-weight:600;font-size:13px;color:#172033">{html.escape(str(item.get('name') or '--'))}</td>
      <td style="padding:8px 10px;text-align:center">{_html_badge(action, color)}</td>
      <td style="padding:8px 10px;text-align:right;font-size:13px;font-weight:700;color:#172033">{_fmt_value(item.get('decision_score'))}</td>
      <td style="padding:8px 10px;text-align:right;font-size:13px;color:#172033">{_fmt_value(item.get('current_price'))}</td>
      <td style="padding:8px 10px;text-align:right;font-size:13px;font-weight:600;color:{change_color}">{_fmt_value(item.get('change_pct'), '%')}</td>
      <td style="padding:8px 10px;text-align:right;font-size:13px;color:#172033">{_fmt_value(item.get('turnover'), '%')}</td>
      <td style="padding:8px 10px;text-align:right;font-size:13px;color:#172033">{_fmt_value(item.get('volume_ratio'))}</td>
      <td style="padding:8px 10px;text-align:right;font-size:13px;color:#172033">{_fmt_amount(item.get('amount'))}</td>
      <td style="padding:8px 10px;text-align:right;font-size:13px;color:{pullback_color}">{_fmt_value(item.get('intraday_pullback_pct'), '%')}</td>
      <td style="padding:8px 10px;font-size:12px;color:#172033;line-height:1.5">{html.escape(_fmt_joined(item.get('reasons')))}</td>
      <td style="padding:8px 10px;font-size:12px;color:#667085;line-height:1.5">{html.escape(_fmt_joined(item.get('risks')))}</td>
    </tr>""")
    return "".join(rows)


def _html_overnight_candidate_section(title: str, items: List[dict], color: str) -> str:
    return f"""
<div style="background:#FFFFFF;border:1px solid #E1E7EF;border-radius:10px;padding:20px 24px;margin-bottom:20px">
  <h2 style="color:#172033;font-size:16px;margin:0 0 14px 0;font-weight:600">{html.escape(title)} <span style="color:#667085;font-weight:400;font-size:13px">({len(items)}只)</span></h2>
  <table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;font-size:13px">
    <thead>
      <tr style="background:#E7EDF4;color:#263545;border-top:3px solid {color}">
        <th style="padding:10px;text-align:center;font-weight:600;border-radius:6px 0 0 0">#</th>
        <th style="padding:10px;text-align:left;font-weight:600">代码</th>
        <th style="padding:10px;text-align:left;font-weight:600">名称</th>
        <th style="padding:10px;text-align:center;font-weight:600">评级</th>
        <th style="padding:10px;text-align:right;font-weight:600">得分</th>
        <th style="padding:10px;text-align:right;font-weight:600">现价</th>
        <th style="padding:10px;text-align:right;font-weight:600">涨幅</th>
        <th style="padding:10px;text-align:right;font-weight:600">换手</th>
        <th style="padding:10px;text-align:right;font-weight:600">量比</th>
        <th style="padding:10px;text-align:right;font-weight:600">成交额</th>
        <th style="padding:10px;text-align:right;font-weight:600">回撤</th>
        <th style="padding:10px;text-align:left;font-weight:600">原因</th>
        <th style="padding:10px;text-align:left;font-weight:600;border-radius:0 6px 0 0">风险</th>
      </tr>
    </thead>
    <tbody>{_overnight_candidate_rows(items)}
    </tbody>
  </table>
</div>"""


def _html_overnight_source_section(decision: dict) -> str:
    source_status = decision.get("source_status") or {}
    channel = source_status.get("channel") or {}
    rows = []
    if channel:
        rows.append(f"""
    <tr style="background:#F8FAFC">
      <td style="padding:8px 10px;font-weight:600;color:#172033">通道</td>
      <td style="padding:8px 10px;color:#172033">{html.escape(str(channel.get('status', '--')))}</td>
      <td style="padding:8px 10px;color:#172033">{html.escape(str(channel.get('kind', '--')))}</td>
      <td style="padding:8px 10px;color:#172033">{html.escape(str(channel.get('scope', '--')))}</td>
      <td style="padding:8px 10px;color:#667085">{html.escape(str(channel.get('note', '--')))}</td>
    </tr>""")
    for idx, item in enumerate(source_status.get("quotes") or []):
        bg = "#FFFFFF" if idx % 2 else "#F8FAFC"
        rows.append(f"""
    <tr style="background:{bg}">
      <td style="padding:8px 10px;font-weight:600;color:#172033">{html.escape(str(item.get('source', '--')))}</td>
      <td style="padding:8px 10px;color:#172033">{html.escape(str(item.get('status', '--')))}</td>
      <td style="padding:8px 10px;color:#172033">count={int(item.get('count') or 0)}</td>
      <td style="padding:8px 10px;color:#667085">source</td>
      <td style="padding:8px 10px;color:#667085">{html.escape(str(item.get('error') or '--'))}</td>
    </tr>""")

    extra_lines = []
    if decision.get("empty_reason"):
        extra_lines.append(html.escape(str(decision.get("empty_reason"))))
    extra_lines.extend(html.escape(str(err)) for err in decision.get("errors") or [])
    extra_html = ""
    if extra_lines:
        extra_html = (
            '<p style="color:#667085;font-size:12px;line-height:1.7;margin:12px 0 0 0">'
            + "<br>".join(extra_lines)
            + "</p>"
        )

    return f"""
<div style="background:#FFFFFF;border:1px solid #E1E7EF;border-radius:10px;padding:20px 24px;margin-bottom:20px">
  <h2 style="color:#172033;font-size:16px;margin:0 0 14px 0;font-weight:600">数据源/通道</h2>
  <table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;font-size:13px">
    <thead>
      <tr style="background:#E7EDF4;color:#263545">
        <th style="padding:10px;text-align:left;font-weight:600;border-radius:6px 0 0 0">对象</th>
        <th style="padding:10px;text-align:left;font-weight:600">状态</th>
        <th style="padding:10px;text-align:left;font-weight:600">类型/数量</th>
        <th style="padding:10px;text-align:left;font-weight:600">范围</th>
        <th style="padding:10px;text-align:left;font-weight:600;border-radius:0 6px 0 0">说明</th>
      </tr>
    </thead>
    <tbody>{''.join(rows) if rows else '<tr><td colspan="5" style="padding:14px 10px;text-align:center;color:#667085;background:#F8FAFC">无数据源状态</td></tr>'}
    </tbody>
  </table>
  {extra_html}
</div>"""


def _build_overnight_html_content(decision: dict, buy_items: List[dict], watch_items: List[dict]) -> str:
    status_color = "#16A36A" if decision.get("status") == "completed" else "#F39C12"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#F3F6FA;color:#172033;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif">
<div style="max-width:900px;margin:0 auto;padding:24px">
  <div style="background:#EEF3F8;border:1px solid #D8E2EC;border-radius:12px;padding:28px 32px;margin-bottom:24px">
    <h1 style="color:#172033;font-size:22px;margin:0 0 4px 0;font-weight:700">New France 尾盘隔夜套利</h1>
    <p style="color:#667085;font-size:13px;margin:0 0 16px 0">{html.escape(str(decision.get('date') or '--'))} | 有效窗口 {html.escape(str(decision.get('valid_window') or '--'))}</p>
    <table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-spacing:8px;margin:0 -8px">
      <tr>
        <td style="padding:14px 16px;background:#FFFFFF;border:1px solid #DCE5EF;border-radius:8px;text-align:center">
          <div style="color:#667085;font-size:11px;margin-bottom:4px">状态</div>
          <div style="color:{status_color};font-size:20px;font-weight:700">{html.escape(str(decision.get('status', 'completed')))}</div>
        </td>
        <td style="padding:14px 16px;background:#FFFFFF;border:1px solid #DCE5EF;border-radius:8px;text-align:center">
          <div style="color:#667085;font-size:11px;margin-bottom:4px">BUY</div>
          <div style="color:#E74C3C;font-size:24px;font-weight:700">{int(decision.get('buy_count') or 0)}</div>
        </td>
        <td style="padding:14px 16px;background:#FFFFFF;border:1px solid #DCE5EF;border-radius:8px;text-align:center">
          <div style="color:#667085;font-size:11px;margin-bottom:4px">WATCH</div>
          <div style="color:#3498DB;font-size:24px;font-weight:700">{int(decision.get('watch_count') or 0)}</div>
        </td>
        <td style="padding:14px 16px;background:#FFFFFF;border:1px solid #DCE5EF;border-radius:8px;text-align:center">
          <div style="color:#667085;font-size:11px;margin-bottom:4px">扫描</div>
          <div style="color:#2F87AC;font-size:24px;font-weight:700">{int(decision.get('total_scanned') or 0)}</div>
        </td>
      </tr>
    </table>
    <p style="color:#667085;font-size:12px;line-height:1.6;margin:12px 0 0 0">当日尾盘买入决策；次日09:30-09:35仅用于卖出复盘和参数校准。</p>
  </div>
  {_html_overnight_candidate_section('BUY 候选', buy_items, '#E74C3C')}
  {_html_overnight_candidate_section('WATCH 候选', watch_items, '#3498DB')}
  {_html_overnight_source_section(decision)}
  <div style="text-align:center;padding:18px;color:#667085;font-size:12px">
    <p style="margin:0 0 4px 0">本结果仅供参考，不构成投资建议</p>
  </div>
</div>
</body>
</html>"""


def notify_overnight_decision(decision: dict) -> bool:
    """发送尾盘套利轻量邮件。失败不影响报告落盘。"""
    config = notifier.get_smtp_config()

    buy_items = [item for item in decision.get("results", []) if item.get("action") == "BUY"][:5]
    watch_items = [item for item in decision.get("results", []) if item.get("action") == "WATCH"][:5]
    subject = f"New France 尾盘隔夜套利 {decision.get('date')} BUY={decision.get('buy_count', 0)} WATCH={decision.get('watch_count', 0)}"
    text_content = _build_overnight_text_content(decision, buy_items, watch_items)
    html_content = _build_overnight_html_content(decision, buy_items, watch_items)
    try:
        ok, _message = notifier.send_email(subject, text_content, html_content, config)
        return ok
    except Exception as exc:
        logger.warning("尾盘套利邮件发送失败: %s", exc)
        return False
