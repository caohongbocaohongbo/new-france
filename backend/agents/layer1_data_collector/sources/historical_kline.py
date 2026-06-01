"""
历史K线数据源 — akshare + 新浪备用
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

    try:
        import akshare as ak
        end = datetime.now(BEIJING_TZ)
        start = end - timedelta(days=days + 30)
        df = ak.stock_zh_a_hist(
            symbol=symbol, period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"), adjust="qfq",
        )
        if df is not None and not df.empty:
            return df, "akshare.stock_zh_a_hist(东方财富历史行情)"
    except Exception as e:
        logger.debug(f"akshare历史数据 {symbol} 失败: {e}")

    df = _fetch_hist_sina(symbol, days)
    if df is not None and not df.empty:
        return df, "新浪财经K线API"
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
    return pd.DataFrame(parsed)


def _fetch_hist_eastmoney_direct(symbol: str, days: int = 60) -> Optional[pd.DataFrame]:
    """直接调用东方财富历史 K 线接口，避免 akshare 包装失败时丢失换手率。"""
    import requests as req

    market = "1" if symbol.startswith(("6", "9")) else "0"
    end = datetime.now(BEIJING_TZ)
    start = end - timedelta(days=days + 30)
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
    """新浪K线API备用"""
    import requests as req

    prefix = "sh" if symbol.startswith(("6", "9")) else "sz"
    sina_symbol = f"{prefix}{symbol}"

    url = "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    params = {"symbol": sina_symbol, "scale": "240", "ma": "no", "datalen": str(days)}
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}

    try:
        resp = req.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()
        if not data or not isinstance(data, list) or len(data) < 3:
            return None

        rows = []
        prev_close = None
        for item in data:
            close_val = float(item.get("close", 0))
            pct = 0.0
            if prev_close is not None and prev_close > 0:
                pct = round((close_val - prev_close) / prev_close * 100, 2)
            row = {
                "日期": item.get("day", ""),
                "开盘": float(item.get("open", 0)),
                "收盘": close_val,
                "最高": float(item.get("high", 0)),
                "最低": float(item.get("low", 0)),
                "成交量": float(item.get("volume", 0)),
                "成交额": 0.0, "振幅": 0.0,
                "涨跌幅": pct, "换手率": 0.0,
            }
            rows.append(row)
            prev_close = close_val

        df = pd.DataFrame(rows)
        logger.debug(f"  新浪K线 {symbol} 获取成功, {len(df)}行")
        return df
    except Exception as e:
        logger.debug(f"新浪K线 {symbol} 失败: {e}")
        return None
