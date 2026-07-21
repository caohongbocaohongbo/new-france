"""新浪单股主力资金流，用于抽样核验或小批量查询。"""
import logging
from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FuturesTimeoutError,
    as_completed,
)
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

SINA_URL = (
    "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "MoneyFlow.ssi_ssfx_flzjtj"
)
SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.sina.com.cn/",
}


def _prefix(code: str) -> str:
    return "sh" if str(code).startswith(("6", "9")) else "sz"


def _to_float(value):
    if value is None or value == "" or value == "-":
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def fetch_single_stock_fund_flow_sina(
    code: str,
    timeout: int = 8,
    session: Optional[requests.Session] = None,
) -> Optional[dict]:
    """查询单只股票的新浪主力资金流。"""
    http = session or requests.Session()
    try:
        response = http.get(
            SINA_URL,
            params={"daima": f"{_prefix(code)}{str(code).zfill(6)}"},
            headers=SINA_HEADERS,
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list) or not data:
            return None
        item = data[0] or {}
        r0_in = _to_float(item.get("r0_in"))
        r0_out = _to_float(item.get("r0_out"))
        r1_in = _to_float(item.get("r1_in"))
        r1_out = _to_float(item.get("r1_out"))
        r2_in = _to_float(item.get("r2_in"))
        r2_out = _to_float(item.get("r2_out"))
        r3_in = _to_float(item.get("r3_in"))
        r3_out = _to_float(item.get("r3_out"))
        main_net = (r0_in + r1_in) - (r0_out + r1_out)
        total_amount = r0_in + r0_out + r1_in + r1_out + r2_in + r2_out + r3_in + r3_out
        ratio = (main_net / total_amount * 100) if total_amount > 0 else None
        return {
            "code": str(code).zfill(6),
            "name": str(item.get("name", "")).strip(),
            "price": _to_float(item.get("trade")),
            "main_net_inflow": round(main_net, 2),
            "main_inflow_ratio": None if ratio is None else round(ratio, 4),
            "source": "sina",
        }
    except Exception as exc:
        logger.debug("新浪单股主力资金失败 %s: %s", code, exc)
        return None


def fetch_codes_fund_flow_sina(
    codes: List[str],
    max_workers: int = 10,
    timeout: int = 8,
    batch_timeout: float = 30.0,
) -> List[dict]:
    """并发查询多只股票的新浪主力资金。

    batch_timeout: 整批抓取的墙钟上限，超时则返回已拿到的部分并放弃
    剩余（新浪本就是抽样核验用途），避免个别慢股把整批 join 拖到分钟级。
    """
    results: List[dict] = []
    if not codes:
        return results
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_single_stock_fund_flow_sina, code, timeout): code
            for code in codes
        }
        try:
            for future in as_completed(futures, timeout=batch_timeout):
                try:
                    item = future.result()
                except Exception:
                    continue
                if item:
                    results.append(item)
        except FuturesTimeoutError:
            logger.warning(
                "新浪主力资金抽样超出墙钟上限 %ss，返回已拿到的 %d 条",
                batch_timeout, len(results),
            )
    return results
