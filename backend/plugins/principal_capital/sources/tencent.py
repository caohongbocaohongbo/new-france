"""腾讯单股主力资金流，用于独立核验或小批量兜底。"""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

TENCENT_URL = "http://qt.gtimg.cn/q="
TENCENT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://stockapp.finance.qq.com/",
}


def _prefix(code: str) -> str:
    return "sh" if str(code).startswith(("6", "9")) else "sz"


def _to_float(value) -> float:
    if value is None or value == "" or value == "-":
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _parse_response_text(text: str) -> Optional[list[str]]:
    if not text or "=" not in text:
        return None
    raw = text.split("=", 1)[1].strip().strip(";").strip('"')
    if not raw:
        return None
    parts = raw.split("~")
    if len(parts) < 14:
        return None
    return parts


def fetch_single_stock_fund_flow_tencent(
    code: str,
    timeout: int = 8,
    session: Optional[requests.Session] = None,
) -> Optional[dict]:
    """查询单只股票的腾讯主力资金流。

    字段索引来自社区逆向，如响应字段变化，需按实际返回重新校正。
    当前按 3=主力净流入、9=资金流总和 解析，原始单位为万元，这里统一换算为元。
    """
    http = session or requests.Session()
    normalized_code = str(code).zfill(6)
    try:
        response = http.get(
            TENCENT_URL,
            params={"q": f"ff_{_prefix(normalized_code)}{normalized_code}"},
            headers=TENCENT_HEADERS,
            timeout=timeout,
        )
        response.raise_for_status()
        response.encoding = "gbk"
        parts = _parse_response_text(response.text)
        if not parts:
            return None
        main_net_wan = _to_float(parts[3])
        total_amount_wan = _to_float(parts[9])
        main_net_inflow = round(main_net_wan * 10000, 2)
        total_amount = round(total_amount_wan * 10000, 2)
        ratio = None
        if total_amount > 0:
            ratio = round(main_net_inflow / total_amount * 100, 4)
        return {
            "code": normalized_code,
            "name": str(parts[12]).strip(),
            "main_net_inflow": main_net_inflow,
            "main_inflow_ratio": ratio,
            "source": "tencent",
        }
    except Exception as exc:
        logger.debug("腾讯单股主力资金失败 %s: %s", normalized_code, exc)
        return None


def fetch_codes_fund_flow_tencent(
    codes: List[str],
    max_workers: int = 10,
    timeout: int = 8,
) -> List[dict]:
    """并发查询多只股票的腾讯主力资金。"""
    results: List[dict] = []
    if not codes:
        return results
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_single_stock_fund_flow_tencent, code, timeout): code
            for code in codes
        }
        for future in as_completed(futures):
            item = future.result()
            if item:
                results.append(item)
    return results
