"""
历史K线数据源 — 明确复权能力，未知口径不进入前复权消费接口。
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
import pandas as pd

BEIJING_TZ = timezone(timedelta(hours=8))

logger = logging.getLogger(__name__)


def fetch_historical(symbol: str, days: int = 60) -> Optional[pd.DataFrame]:
    """获取个股历史日线数据（前复权）"""
    result = fetch_historical_with_source(symbol, days)
    if result is None:
        return None
    df, _source = result
    return df


def fetch_historical_with_source(symbol: str, days: int = 60) -> Optional[tuple[pd.DataFrame, str]]:
    """获取个股历史日线数据，并返回可追溯数据源名称。"""
    df = _fetch_hist_eastmoney_direct(symbol, days)
    if df is not None and not df.empty:
        return df, "东方财富历史K线API(push2his)"

    # 东财直连失败后不再通过AKShare重打同一上游；新浪轻量端点复权未知。
    df = _fetch_hist_tencent(symbol, days)
    if df is not None and not df.empty:
        return df, "腾讯前复权K线API"
    return None


def _parse_eastmoney_kline_rows(rows: list[str]) -> pd.DataFrame:
    """解析东方财富 push2his K 线行，保留真实换手率字段。"""
    parsed = []
    for item in rows or []:
        parts = str(item).split(",")
        if len(parts) < 11:
            continue
        try:
            parsed.append({
                "日期": parts[0],
                "开盘": float(parts[1]),
                "收盘": float(parts[2]),
                "最高": float(parts[3]),
                "最低": float(parts[4]),
                "成交量": float(parts[5]),
                "成交额": float(parts[6]),
                "振幅": float(parts[7]),
                "涨跌幅": float(parts[8]),
                "涨跌额": float(parts[9]),
                "换手率": float(parts[10]),
            })
        except (TypeError, ValueError):
            continue
    df = pd.DataFrame(parsed)
    df.attrs.update({"adjustment": "qfq", "volume_unit": "lot", "source": "eastmoney"})
    return df


def _fetch_hist_eastmoney_direct(symbol: str, days: int = 60) -> Optional[pd.DataFrame]:
    """直接调用东方财富历史 K 线接口，避免 akshare 包装失败时丢失换手率。"""
    import requests as req

    market = "1" if symbol.startswith(("6", "9")) else "0"
    end = datetime.now(BEIJING_TZ)
    start = end - timedelta(days=days * 2 + 30)
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": f"{market}.{symbol}",
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "1",
        "beg": start.strftime("%Y%m%d"),
        "end": end.strftime("%Y%m%d"),
    }
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}

    try:
        resp = req.get(url, params=params, headers=headers, timeout=12)
        resp.raise_for_status()
        rows = resp.json().get("data", {}).get("klines") or []
        df = _parse_eastmoney_kline_rows(rows)
        if df.empty:
            return None
        logger.debug(f"  东方财富K线 {symbol} 获取成功, {len(df)}行")
        return df.tail(days)
    except Exception as e:
        logger.debug(f"东方财富K线直接接口 {symbol} 失败: {e}")
        return None


def _fetch_hist_sina(symbol: str, days: int = 60) -> Optional[pd.DataFrame]:
    """新浪轻量日K，仅供核验；未证明复权，不作为qfq兜底。"""
    import requests as req

    prefix = "sh" if symbol.startswith(("6", "9")) else "sz"
    sina_symbol = f"{prefix}{symbol}"

    url = "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    params = {"symbol": sina_symbol, "scale": "240", "ma": "no", "datalen": str(days)}
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}

    try:
        resp = req.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not data or not isinstance(data, list) or len(data) < 3:
            return None

        rows = []
        prev_close = None
        for item in data:
            close_val = float(item.get("close", 0))
            pct = None
            if prev_close is not None and prev_close > 0:
                pct = round((close_val - prev_close) / prev_close * 100, 2)
            row = {
                "日期": item.get("day", ""),
                "开盘": float(item.get("open", 0)),
                "收盘": close_val,
                "最高": float(item.get("high", 0)),
                "最低": float(item.get("low", 0)),
                "成交量": float(item.get("volume", 0)) / 100,
                "成交额": None, "振幅": None,
                "涨跌幅": pct, "换手率": None,
            }
            rows.append(row)
            prev_close = close_val

        df = pd.DataFrame(rows)
        df.attrs.update({"adjustment": "unknown", "volume_unit": "lot", "source": "sina",
                         "missing_fields": ["成交额", "换手率", "振幅"], "degraded": True})
        logger.debug(f"  新浪K线 {symbol} 获取成功, {len(df)}行")
        return df
    except Exception as e:
        logger.debug(f"新浪K线 {symbol} 失败: {e}")
        return None


def _fetch_hist_tencent(symbol: str, days: int = 60) -> Optional[pd.DataFrame]:
    """仅接收qfqday；不以day替代，缺额/换手保持空值。"""
    import requests
    from .quote_contract import number

    if not str(symbol).isdigit() or len(str(symbol)) != 6 or not 1 <= days <= 600:
        return None
    code = ("sh" if symbol.startswith(("6", "9")) else "sz") + symbol
    try:
        response = requests.get(
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            params={"param": f"{code},day,,,{days},qfq"},
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}, timeout=(3, 8),
        )
        response.raise_for_status()
        bars = response.json().get("data", {}).get(code, {}).get("qfqday")
        if not isinstance(bars, list) or not bars:
            return None
        rows = []
        for bar in bars:
            if not isinstance(bar, list) or len(bar) < 6:
                raise ValueError("腾讯K线结构变化")
            values = [number(value) for value in bar[1:6]]
            if any(value is None for value in values) or values[4] < 0:
                raise ValueError("腾讯K线数值缺失")
            opening, close, high, low, volume = values
            if min(opening, close, low) <= 0 or high < max(opening, close) or low > min(opening, close):
                raise ValueError("腾讯K线OHLC不一致")
            day = datetime.strptime(bar[0], "%Y-%m-%d").date().isoformat()
            if rows and day <= rows[-1]["日期"]:
                raise ValueError("腾讯K线日期重复或乱序")
            rows.append({"日期": day, "开盘": opening, "收盘": close, "最高": high, "最低": low,
                         "成交量": volume, "成交额": None, "换手率": None,
                         "振幅": None, "涨跌幅": None})
        df = pd.DataFrame(rows).tail(days)
        df["涨跌幅"] = df["收盘"].pct_change(fill_method=None) * 100
        df.attrs.update({"source": "tencent", "adjustment": "qfq", "volume_unit": "lot",
                         "missing_fields": ["成交额", "换手率", "振幅"], "degraded": True})
        return df
    except Exception as exc:
        logger.debug("腾讯前复权K线 %s 失败: %s", symbol, exc)
        return None
