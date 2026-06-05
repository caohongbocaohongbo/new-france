"""尾盘隔夜套利策略服务。"""
import html
import json
import logging
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
REPORT_FILE = PROJECT_DIR / "reports" / "overnight_arbitrage_latest.json"
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


def _is_excluded_market(code: str) -> bool:
    return code.startswith(("300", "301"))


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
        "current_price": _float(row.get("最新价")),
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
    return {
        "status": "completed",
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
            "eastmoney_quote": {"status": "ok" if total else "empty", "count": total},
            "eastmoney_zt_pool": {"status": "ok" if zt_map else "optional_missing", "count": len(zt_map)},
            "sina_quote": {"status": "fallback_in_quote_source"},
            "yahoo_5m": {"status": "ok" if minute_strength else "optional_missing", "count": len(minute_strength)},
        },
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


def _eastmoney_all_a_snapshot() -> pd.DataFrame:
    """拉取沪深全 A 实时快照，供尾盘套利粗筛。"""
    import requests

    url = "https://push2.eastmoney.com/api/qt/clist/get"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/center/gridlist.html"}
    fields = "f2,f3,f5,f6,f8,f10,f12,f14,f20,f21"
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
                    "成交量": _float(item.get("f5")),
                    "成交额": _float(item.get("f6")),
                    "换手率": _float(item.get("f8")),
                    "量比": _float(item.get("f10")),
                    "总市值": _float(item.get("f20")),
                    "流通市值": _float(item.get("f21")),
                })
    return pd.DataFrame(rows)


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
    quotes = pd.DataFrame()
    zt_pool = pd.DataFrame()
    minute_strength = {}
    try:
        quotes = quote_fetcher()
    except Exception as exc:
        logger.exception("尾盘套利全市场行情失败: %s", exc)
        errors.append(f"全市场行情失败: {exc}")
    try:
        zt_pool = zt_fetcher(target_date)
    except TypeError:
        zt_pool = zt_fetcher()
    except Exception as exc:
        logger.warning("尾盘套利涨停池失败: %s", exc)
        errors.append(f"涨停池失败: {exc}")

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
    )
    if errors:
        decision["errors"] = errors
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
