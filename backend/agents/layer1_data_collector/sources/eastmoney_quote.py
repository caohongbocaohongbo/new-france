"""
实时行情数据源 — 批量查询股票行情
"""
import logging
from typing import List
import pandas as pd

logger = logging.getLogger(__name__)


def fetch_stock_quotes(codes: List[str]) -> pd.DataFrame:
    """批量查询指定股票的实时行情"""
    if not codes:
        return pd.DataFrame()

    secids = []
    for code in codes:
        market = "1" if code.startswith(("6", "9")) else "0"
        secids.append(f"{market}.{code}")

    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    params = {
        "fields": "f2,f3,f5,f6,f8,f9,f10,f12,f14,f20,f21",
        "secids": ",".join(secids),
        "fltt": "2", "invt": "2",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://quote.eastmoney.com/",
    }

    try:
        from curl_cffi import requests as curl_req
        use_curl = True
    except ImportError:
        import requests as curl_req
        use_curl = False

    kwargs = dict(params=params, headers=headers, timeout=15)
    if use_curl:
        resp = curl_req.get(url, impersonate="chrome120", **kwargs)
    else:
        resp = curl_req.get(url, **kwargs)
    data = resp.json()
    items = data.get("data", {}).get("diff", [])

    rows = []
    for item in items:
        code = str(item.get("f12", "")).zfill(6)
        name = str(item.get("f14", ""))

        def _float(key):
            v = item.get(key)
            if v is None or v == "-":
                return None
            try:
                return float(v)
            except (ValueError, TypeError):
                return None

        rows.append({
            "代码": code, "名称": name,
            "最新价": _float("f2"), "涨跌幅": _float("f3"),
            "成交量": _float("f5"), "成交额": _float("f6"),
            "换手率": _float("f8"), "市盈率": _float("f9"),
            "量比": _float("f10"), "总市值": _float("f20"),
            "流通市值": _float("f21"),
        })

    return pd.DataFrame(rows)
