"""尾盘隔夜套利策略服务。"""
import html
import json
import logging
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
REPORT_FILE = PROJECT_DIR / "reports" / "overnight_arbitrage_latest.json"
HISTORY_FILE = PROJECT_DIR / "reports" / "overnight_arbitrage_history.json"
BEIJING_TZ = timezone(timedelta(hours=8))

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
    if amount < 80_000_000:
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
    if score >= 55:
        action = "BUY"
    elif score >= 36 or (change_pct >= 7 and amount >= 150_000_000):
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
            "quotes": quote_source_status or [_empty_source_status("eastmoney_all_a", "ok" if total else "empty", total)],
            "eastmoney_zt_pool": {"status": "ok" if zt_map else "optional_missing", "count": len(zt_map)},
            "yahoo_5m": {"status": "ok" if minute_strength else "optional_missing", "count": len(minute_strength)},
        },
        "errors": errors,
        "empty_reason": "" if has_quotes else "全市场行情主备源均不可用或返回空数据，未扫描到可判断股票",
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
        from ..agents.layer1_data_collector.sources.eastmoney_zt import fetch_zt_pool
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


def notify_overnight_decision(decision: dict) -> bool:
    """发送尾盘套利轻量邮件。失败不影响报告落盘。"""
    try:
        from ..agents.layer3_recommendation import notifier
    except Exception as exc:
        logger.warning("尾盘套利邮件模块不可用: %s", exc)
        return False

    config = notifier._get_notify_config()
    if not config.get("email_enabled") or not config.get("email_to"):
        return False

    buy_items = [item for item in decision.get("results", []) if item.get("action") == "BUY"][:5]
    watch_items = [item for item in decision.get("results", []) if item.get("action") == "WATCH"][:5]
    subject = f"New France 尾盘隔夜套利 {decision.get('date')} BUY={decision.get('buy_count', 0)} WATCH={decision.get('watch_count', 0)}"
    lines = [
        "【尾盘隔夜套利 14:43 决策】",
        f"日期: {decision.get('date')} | 有效窗口: {decision.get('valid_window')}",
        f"状态: {decision.get('status', 'completed')} | 扫描: {decision.get('total_scanned', 0)}",
        "说明: 当日尾盘买入决策；次日09:30-09:35仅用于卖出复盘和参数校准。",
        "",
        "【可买】",
    ]
    if buy_items:
        for item in buy_items:
            lines.append(
                f"{item.get('code')} {item.get('name')} 分数{item.get('decision_score')} "
                f"涨幅{item.get('change_pct')}% 量比{item.get('volume_ratio')} "
                f"风险:{'、'.join(item.get('risks') or ['--'])}"
            )
    else:
        lines.append("无")
    lines.append("")
    lines.append("【观察】")
    if watch_items:
        for item in watch_items:
            lines.append(
                f"{item.get('code')} {item.get('name')} 分数{item.get('decision_score')} "
                f"原因:{'、'.join(item.get('reasons') or ['--'])}"
            )
    else:
        lines.append("无")

    if decision.get("errors") or decision.get("empty_reason"):
        lines.append("")
        lines.append("【数据源/空仓说明】")
        if decision.get("empty_reason"):
            lines.append(str(decision.get("empty_reason")))
        for err in decision.get("errors") or []:
            lines.append(f"- {err}")

    text_content = "\n".join(lines)
    html_content = (
        '<!DOCTYPE html><html lang="zh-CN"><body style="font-family:sans-serif;padding:20px">'
        '<pre style="white-space:pre-wrap;line-height:1.7;color:#172033">'
        f"{html.escape(text_content)}"
        "</pre></body></html>"
    )
    try:
        ok, _message = notifier._send_email(
            subject=subject,
            text_content=text_content,
            html_content=html_content,
            notify_config=config,
        )
        return ok
    except Exception as exc:
        logger.warning("尾盘套利邮件发送失败: %s", exc)
        return False
