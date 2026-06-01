"""指数数据源 — 获取上证指数点位与涨跌幅。"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)
BEIJING_TZ = timezone(timedelta(hours=8))


def fetch_index_snapshot() -> Dict[str, Any]:
    """获取上证指数当前点位快照，字段均来自真实行情源。"""
    result = _fetch_from_eastmoney()
    if result is not None:
        logger.info(
            "  上证指数: %s (%+.2f%%, 东方财富)",
            _format_index_value(result.get("value")),
            result.get("gain_pct") or 0.0,
        )
        return result

    result = _fetch_from_sina()
    if result is not None:
        logger.info(
            "  上证指数: %s (%+.2f%%, 新浪)",
            _format_index_value(result.get("value")),
            result.get("gain_pct") or 0.0,
        )
        return result

    logger.error("所有指数数据源均失败，无法返回真实指数点位")
    return {
        "code": "000001",
        "name": "上证指数",
        "value": None,
        "change": None,
        "gain_pct": None,
        "source": None,
        "fetched_at": datetime.now(BEIJING_TZ).isoformat(),
    }


def fetch_index_gain() -> float:
    """获取上证指数当日涨跌幅，兼容旧调用方。"""
    snapshot = fetch_index_snapshot()
    gain = snapshot.get("gain_pct")
    if gain is not None:
        return float(gain)

    logger.error("所有指数数据源均失败，返回 0.0")
    return 0.0


def _format_index_value(value: Optional[float]) -> str:
    return "--" if value is None else f"{value:.2f}"


def _as_float(value) -> Optional[float]:
    if value is None or value == "-":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _snapshot(name: str, value: Optional[float], change: Optional[float],
              gain_pct: Optional[float], source: str) -> Dict[str, Any]:
    return {
        "code": "000001",
        "name": name or "上证指数",
        "value": round(value, 2) if value is not None else None,
        "change": round(change, 2) if change is not None else None,
        "gain_pct": round(gain_pct, 2) if gain_pct is not None else None,
        "source": source,
        "fetched_at": datetime.now(BEIJING_TZ).isoformat(),
    }


def _fetch_from_eastmoney() -> Optional[Dict[str, Any]]:
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
            value = _as_float(d.get("f43"))
            change = _as_float(d.get("f169"))
            pct = _as_float(d.get("f170"))
            if pct is None and value is not None and change is not None:
                prev_close = value - change
                if prev_close > 0:
                    pct = change / prev_close * 100
            if value is not None or pct is not None:
                return _snapshot(d.get("f58") or "上证指数", value, change, pct, "东方财富")
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
            value = _as_float(d.get("f43"))
            change = _as_float(d.get("f169"))
            pct = _as_float(d.get("f170"))
            if pct is None and value is not None and change is not None:
                prev_close = value - change
                if prev_close > 0:
                    pct = change / prev_close * 100
            if value is not None or pct is not None:
                return _snapshot(d.get("f58") or "上证指数", value, change, pct, "东方财富")
    except Exception as e:
        logger.warning(f"东方财富(curl_cffi) 获取指数失败: {e}")

    return None


def _fetch_from_sina() -> Optional[Dict[str, Any]]:
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
                value = _as_float(parts[1])
                change = _as_float(parts[2])
                pct = _as_float(parts[3].replace("%", ""))
                return _snapshot(parts[0], value, change, pct, "新浪财经")
    except Exception as e:
        logger.warning(f"新浪财经获取指数失败: {e}")
    return None
