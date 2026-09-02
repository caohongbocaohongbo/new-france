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
DEFAULT_FIELDS = "f12,f14,f2,f3,f5,f6,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f21"
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
            "vol": _to_float(item.get("f5")),
            "total_amount": total_amount,
            "main_net_inflow": main_net_inflow,
            "main_inflow_ratio": main_ratio,
            "super_net": super_net,
            "big_net": big_net,
            "mid_net": _to_float(item.get("f78")),
            "small_net": _to_float(item.get("f84")),
            "float_mcap": _to_float(item.get("f21")),
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
    # 收紧策略：单请求超时短、不重试。宁可整轮快速失败并 skip，
    # 也不让 timeout×retry×多页 累积到分钟级把整个 job 拖到被强杀。
    response = http.get(
        base_url,
        params=_build_params(fs, page),
        headers=DEFAULT_HEADERS,
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    return (payload.get("data") or {}).get("diff") or []


def fetch_market_fund_flow(
    timeout: int = 5,
    session: Optional[requests.Session] = None,
    base_url: str = DEFAULT_BASE_URL,
    budget_seconds: float = 60.0,
) -> pd.DataFrame:
    """抓取东方财富全市场主力资金流。

    budget_seconds: 整轮抓取的墙钟预算，超出即抛异常终止本轮（由上层
    捕获后 skip），避免慢响应累积把 GitHub Actions job 拖到被强杀。
    """
    http = session or requests.Session()
    rows: list[dict] = []
    deadline = time.monotonic() + budget_seconds
    try:
        for fs in DEFAULT_FS_GROUPS:
            for page in range(1, 8):
                if time.monotonic() > deadline:
                    raise FundFlowFetchError(
                        f"东方财富抓取超出墙钟预算 {budget_seconds}s，本轮放弃"
                    )
                diff = _get_page(http, base_url, fs, page, timeout)
                if not diff:
                    break
                rows.extend(_parse_diff(diff))
                if len(diff) < 200:
                    break
    except FundFlowFetchError:
        raise
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
            "code", "name", "price", "change_pct", "vol", "total_amount",
            "main_net_inflow", "main_inflow_ratio", "super_net", "big_net",
            "mid_net", "small_net", "float_mcap", "source",
        ])
    df = df.drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)
    return df
