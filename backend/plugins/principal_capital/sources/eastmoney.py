"""东方财富全市场主力资金流。"""
import logging
import time
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://push2.eastmoney.com/api/qt/clist/get"
EASTMONEY_HOSTS = [
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://push2his.eastmoney.com/api/qt/clist/get",
    "https://62.push2.eastmoney.com/api/qt/clist/get",
]
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://data.eastmoney.com/zjlx/list.html",
}
DEFAULT_UT = "bd1d9ddb04089700cf9c27f6f7426281"
DEFAULT_FIELDS = "f12,f14,f2,f3,f6,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87"
DEFAULT_FS_GROUPS = [
    "m:1+t:2,m:1+t:23",
    "m:0+t:6,m:0+t:80",
]


class FundFlowFetchError(Exception):
    """数据源拉取失败，触发熔断切换。"""


def _to_float(value):
    if value is None or value == "" or value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_params(fs: str, page: int) -> dict:
    return {
        "pn": str(page),
        "pz": "200",
        "po": "1",
        "fid": "f62",
        "fs": fs,
        "fields": DEFAULT_FIELDS,
        "ut": DEFAULT_UT,
        "fltt": "2",
        "invt": "2",
    }


def _parse_diff(items: list[dict]) -> list[dict]:
    rows = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("f12", "")).zfill(6)
        if not code:
            continue
        total_amount = _to_float(item.get("f6"))
        main_net_inflow = _to_float(item.get("f62"))
        main_ratio = _to_float(item.get("f184"))
        super_net = _to_float(item.get("f66"))
        big_net = _to_float(item.get("f72"))
        if main_ratio is None and total_amount and total_amount > 0:
            fallback = ((_to_float(super_net) or 0.0) + (_to_float(big_net) or 0.0)) / total_amount * 100
            main_ratio = round(fallback, 4)
        rows.append({
            "code": code,
            "name": str(item.get("f14", "")).strip(),
            "price": _to_float(item.get("f2")),
            "change_pct": _to_float(item.get("f3")),
            "total_amount": total_amount,
            "main_net_inflow": main_net_inflow,
            "main_inflow_ratio": main_ratio,
            "super_net": super_net,
            "big_net": big_net,
            "mid_net": _to_float(item.get("f78")),
            "small_net": _to_float(item.get("f84")),
            "source": "eastmoney",
        })
    return rows


def _get_page(
    http: requests.Session,
    base_url: str,
    fs: str,
    page: int,
    timeout: int,
) -> list[dict]:
    last_error = None
    for attempt in range(2):
        try:
            response = http.get(
                base_url,
                params=_build_params(fs, page),
                headers=DEFAULT_HEADERS,
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            return (payload.get("data") or {}).get("diff") or []
        except requests.RequestException as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.5)
                continue
            raise
    if last_error:
        raise last_error
    return []


def fetch_market_fund_flow(
    timeout: int = 10,
    session: Optional[requests.Session] = None,
    base_url: str = DEFAULT_BASE_URL,
) -> pd.DataFrame:
    """抓取东方财富全市场主力资金流。"""
    http = session or requests.Session()
    rows: list[dict] = []
    try:
        for fs in DEFAULT_FS_GROUPS:
            for page in range(1, 8):
                diff = _get_page(http, base_url, fs, page, timeout)
                if not diff:
                    break
                rows.extend(_parse_diff(diff))
                if len(diff) < 200:
                    break
    except requests.RequestException as exc:
        logger.warning("东方财富主力资金抓取失败: %s", exc)
        raise FundFlowFetchError(str(exc)) from exc
    except ValueError as exc:
        logger.warning("东方财富主力资金响应解析失败: %s", exc)
        raise FundFlowFetchError(str(exc)) from exc
    except Exception as exc:
        logger.warning("东方财富主力资金未知异常: %s", exc)
        raise FundFlowFetchError(str(exc)) from exc

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=[
            "code", "name", "price", "change_pct", "total_amount",
            "main_net_inflow", "main_inflow_ratio", "super_net", "big_net",
            "mid_net", "small_net", "source",
        ])
    df = df.drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)
    return df
