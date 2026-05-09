"""
历史K线数据源 — akshare + 新浪备用
"""
import logging
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd

logger = logging.getLogger(__name__)


def fetch_historical(symbol: str, days: int = 60) -> Optional[pd.DataFrame]:
    """获取个股历史日线数据（前复权）"""
    try:
        import akshare as ak
        end = datetime.now()
        start = end - timedelta(days=days + 30)
        df = ak.stock_zh_a_hist(
            symbol=symbol, period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"), adjust="qfq",
        )
        if df is not None and not df.empty:
            return df
    except Exception as e:
        logger.debug(f"akshare历史数据 {symbol} 失败: {e}")

    return _fetch_hist_sina(symbol, days)


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
