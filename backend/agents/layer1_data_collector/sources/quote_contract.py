"""报价端点契约与基础涨停状态；不推断封板事件或资金分类。"""
from __future__ import annotations

import math
import re
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation

BEIJING_TZ = timezone(timedelta(hours=8))


def number(value):
    """拒绝空值、布尔值和非有限数，保留合法零。"""
    if value is None or isinstance(value, bool) or str(value).strip() in {"", "-", "--"}:
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def scaled(value, factor):
    result = number(value)
    return None if result is None else result * factor


def normalize_sina_bulk(rows):
    """仅标准化报价观察值；bulk时间及分类不足，不作为正式资金源。"""
    if not isinstance(rows, list) or not rows:
        raise ValueError("新浪bulk不是非空列表")
    result = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("新浪bulk包含非对象记录")
        symbol = str(row.get("symbol", ""))
        if not re.fullmatch(r"(?:sh60|sh688|sz00[0-3]|sz30[01])\d+", symbol) or len(symbol) != 8:
            continue
        if symbol in seen:
            raise ValueError(f"新浪bulk重复证券: {symbol}")
        seen.add(symbol)
        result.append({
            "symbol": symbol, "code": symbol[2:], "name": row.get("name"),
            "price": number(row.get("trade")),
            "change_pct": scaled(row.get("changeratio"), 100),
            # 此端点原值为基点；与新浪其他端点的比例值不可混用。
            "turnover_pct": scaled(row.get("turnover"), 0.01),
            "amount": number(row.get("amount")),
            "source_time": None, "source": "sina_bulk",
            "degraded_reasons": ["source_time_unknown", "fund_flow_classification_unverified"],
        })
    if not result:
        raise ValueError("新浪bulk没有可识别的沪深A股")
    return result


def parse_tencent_quotes(text, symbols):
    """腾讯报价转既有中文列契约：成交量保持手，金额/市值转元。"""
    wanted = set(symbols)
    result = {}
    for symbol, raw in re.findall(r'v_((?:sh|sz)\d{6})="([^"\r\n]*)"', text):
        if symbol not in wanted:
            continue
        if symbol in result:
            raise ValueError(f"腾讯报价重复证券: {symbol}")
        fields = raw.split("~")
        if len(fields) <= 48 or fields[2] != symbol[2:] or not fields[1]:
            continue
        at = lambda i: fields[i] if i < len(fields) else None
        try:
            source_time = datetime.strptime(fields[30], "%Y%m%d%H%M%S").replace(tzinfo=BEIJING_TZ)
        except ValueError:
            continue
        price, prev = number(at(3)), number(at(4))
        if price is None or price <= 0 or prev is None or prev <= 0:
            continue
        row = {
            "代码": symbol[2:], "名称": fields[1], "最新价": price, "昨收": prev,
            "涨跌幅": number(at(32)), "成交量": number(at(6)),
            "成交额": scaled(at(37), 10000), "换手率": number(at(38)),
            "市盈率": number(at(39)), "量比": number(at(49)),
            "总市值": scaled(at(45), 100000000), "流通市值": scaled(at(44), 100000000),
            "最高价": number(at(33)), "最低价": number(at(34)),
            "涨停价": number(at(47)), "跌停价": number(at(48)),
            "source": "tencent", "source_time": source_time.isoformat(),
        }
        required = ("涨跌幅", "成交量", "成交额", "换手率", "总市值", "流通市值")
        for key in required[1:]:
            if row[key] is not None and row[key] < 0:
                row[key] = None
        row["missing_fields"] = [key for key in required if row[key] is None]
        row["degraded"] = bool(row["missing_fields"])
        result[symbol] = row
    return [result[symbol] for symbol in dict.fromkeys(symbols) if symbol in result]


def quote_is_current(row, now=None, max_age_seconds=45):
    """盘中校验真实行情时间；午休/盘后允许对应收盘时点快照。"""
    now = (now or datetime.now(BEIJING_TZ)).astimezone(BEIJING_TZ)
    try:
        stamp = datetime.fromisoformat(row["source_time"])
        if stamp.tzinfo is None:
            return False
        stamp = stamp.astimezone(BEIJING_TZ)
    except (KeyError, TypeError, ValueError):
        return False
    if stamp.date() != now.date() or (stamp - now).total_seconds() > 5:
        return False
    cutoff = now
    if time(11, 30) <= now.time() < time(13):
        cutoff = now.replace(hour=11, minute=30, second=0, microsecond=0)
    elif now.time() >= time(15):
        cutoff = now.replace(hour=15, minute=0, second=0, microsecond=0)
    return (cutoff - stamp).total_seconds() <= max_age_seconds


def at_limit_price(price, limit_price):
    """仅判断最新价是否在有效涨停价；未知返回None，不推断ST或新股规则。"""
    try:
        current, upper = Decimal(str(price)), Decimal(str(limit_price))
        if not current.is_finite() or not upper.is_finite() or current <= 0 or upper <= 0:
            return None
        # 股票报价须落在分位；不通过四舍五入把接近涨停伪装成涨停。
        if current != current.quantize(Decimal("0.01")) or upper != upper.quantize(Decimal("0.01")):
            return None
        return current == upper
    except (InvalidOperation, ValueError):
        return None
