"""
指数数据源 — 获取上证指数涨跌幅
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def fetch_index_gain() -> float:
    """获取上证指数当日涨跌幅，多重fallback保证可用"""
    # 方案1: 东方财富 API (最稳定, 无额外依赖)
    result = _fetch_from_eastmoney()
    if result is not None:
        logger.info(f"  上证指数涨幅: {result:+.2f}% (东方财富)")
        return result

    # 方案2: 新浪 API
    result = _fetch_from_sina()
    if result is not None:
        logger.info(f"  上证指数涨幅: {result:+.2f}% (新浪)")
        return result

    logger.error("所有指数数据源均失败，返回 0.0")
    return 0.0


def _fetch_from_eastmoney() -> Optional[float]:
    """东方财富 API — 优先使用 requests，备选 curl_cffi"""
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "secid": "1.000001",
        "fields": "f43,f44,f45,f46,f47,f48,f50,f51,f52,f57,f58,f60,f107,f168,f169,f170,f171",
        "ut": "fa5fd1943c7b386f172d6893dbbd27dc",
        "fltt": "2",
        "invt": "2",
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://quote.eastmoney.com/",
    }

    # 尝试1: 标准 requests
    try:
        import requests
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        d = data.get("data", {})
        if d:
            # f43: 最新价, f169: 涨跌额, f170: 涨跌幅
            pct = d.get("f170")
            if pct is not None and pct != "-":
                return float(pct)
            # fallback: 用最新价和涨跌额计算
            price = d.get("f43")
            change = d.get("f169")
            if price and change and price != "-" and change != "-":
                prev_close = float(price) / 100 - float(change) / 100
                if prev_close > 0:
                    return round(float(change) / prev_close * 100, 2)
    except Exception as e:
        logger.warning(f"东方财富(requests) 获取指数失败: {e}")

    # 尝试2: curl_cffi 模拟 Chrome
    try:
        from curl_cffi import requests as curl_req
        resp = curl_req.get(url, params=params, headers=headers,
                           impersonate="chrome120", timeout=15)
        data = resp.json()
        d = data.get("data", {})
        if d:
            pct = d.get("f170")
            if pct is not None and pct != "-":
                return float(pct)
            price = d.get("f43")
            change = d.get("f169")
            if price and change and price != "-" and change != "-":
                prev_close = float(price) / 100 - float(change) / 100
                if prev_close > 0:
                    return round(float(change) / prev_close * 100, 2)
    except Exception as e:
        logger.warning(f"东方财富(curl_cffi) 获取指数失败: {e}")

    return None


def _fetch_from_sina() -> Optional[float]:
    """新浪财经 API 作为最终 fallback"""
    try:
        import requests
        url = "https://hq.sinajs.cn/list=s_sh000001"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://finance.sina.com.cn/",
        }
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = "gbk"
        text = resp.text
        # 格式: var hq_str_s_sh000001="上证指数,3300.00,10.00,0.30%,..."
        if '"' in text:
            parts = text.split('"')[1].split(",")
            if len(parts) > 3:
                pct_str = parts[3].replace("%", "")
                return float(pct_str)
    except Exception as e:
        logger.warning(f"新浪财经获取指数失败: {e}")
    return None
