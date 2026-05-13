"""
指数数据源 — 获取上证指数涨跌幅
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def fetch_index_gain() -> float:
    """获取上证指数当日涨跌幅"""
    try:
        import akshare as ak
        df = ak.stock_zh_index_spot_em()
        idx = df[df['代码'] == '000001']
        if not idx.empty:
            return float(idx.iloc[0]['涨跌幅'])
    except Exception as e:
        logger.warning(f"akshare 获取指数失败: {e}")

    return _fetch_index_direct()


def _fetch_index_direct() -> float:
    """直连东方财富API (优先用标准 requests，curl_cffi 做备选)"""
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "secid": "1.000001",
        "fields": "f2,f3,f12,f14",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2", "invt": "2",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://quote.eastmoney.com/",
    }

    # 优先标准 requests
    try:
        import requests
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()
        return float(data.get("data", {}).get("f3", 0))
    except Exception:
        pass

    # 备选 curl_cffi
    try:
        from curl_cffi import requests as curl_req
        resp = curl_req.get(url, params=params, headers=headers, timeout=15)
        data = resp.json()
        return float(data.get("data", {}).get("f3", 0))
    except Exception as e:
        logger.warning(f"直连东方财富也失败: {e}")
        return 0.0
