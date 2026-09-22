"""新浪单股主力资金流（23 v2：数据契约校验 + 真实墙钟预算）。

P0-7：必需字段缺失/非法时拒绝该行并记录原因，不得归零伪装有效零流入。
P0-8：batch_timeout 是真实墙钟上限；超时取消未完成任务并 shutdown(wait=False, cancel_futures=True)。
"""
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, FIRST_COMPLETED, wait
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

BEIJING_TZ = timezone(timedelta(hours=8))

SINA_URL = (
    "http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "MoneyFlow.ssi_ssfx_flzjtj"
)
SINA_ENDPOINT = "MoneyFlow.ssi_ssfx_flzjtj"
SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.sina.com.cn/",
}

# 必需资金字段：任一缺失/非法即拒绝该行（total_amount 与 main_net 依赖全部 8 个字段）
_REQUIRED_FIELDS = ("r0_in", "r0_out", "r1_in", "r1_out", "r2_in", "r2_out", "r3_in", "r3_out")


def _now_beijing(now: Optional[datetime] = None) -> datetime:
    if now is None:
        return datetime.now(BEIJING_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=BEIJING_TZ)
    return now


def _prefix(code: str) -> str:
    return "sh" if str(code).startswith(("6", "9")) else "sz"


def _to_float(value):
    """非法输入（None/空串/-/NaN/Inf）返回 None，不得归零。"""
    if value is None or value == "" or value == "-":
        return None
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_sina_flow_item(item, code: str, fetched_at: datetime):
    """解析单股响应为数据契约行。返回 (row, rejection_reason)。"""
    if not isinstance(item, dict):
        return None, "non_dict_response"
    values = {}
    missing = []
    for field in _REQUIRED_FIELDS:
        value = _to_float(item.get(field))
        if value is None:
            missing.append(field)
        else:
            values[field] = value
    if missing:
        return None, "missing_required_field:" + ",".join(sorted(missing))

    super_net = values["r0_in"] - values["r0_out"]
    big_net = values["r1_in"] - values["r1_out"]
    mid_net = values["r2_in"] - values["r2_out"]
    small_net = values["r3_in"] - values["r3_out"]
    main_net = super_net + big_net
    total_amount = sum(values[field] for field in _REQUIRED_FIELDS)
    ratio = (main_net / total_amount * 100) if total_amount > 0 else None

    price = _to_float(item.get("trade"))
    changeratio = _to_float(item.get("changeratio"))
    change_pct = round(changeratio * 100, 4) if changeratio is not None else None

    row = {
        "code": str(code).zfill(6),
        "name": str(item.get("name") or "").strip(),
        "price": price,
        "change_pct": change_pct,
        "total_amount": round(total_amount, 2),
        "main_net_inflow": round(main_net, 2),
        "main_inflow_ratio": None if ratio is None else round(ratio, 4),
        "super_net": round(super_net, 2),
        "big_net": round(big_net, 2),
        "mid_net": round(mid_net, 2),
        "small_net": round(small_net, 2),
        "source": "sina_single",
        "endpoint": SINA_ENDPOINT,
        "fetched_at": fetched_at.isoformat(),
        "source_time": None,
        "freshness_basis": "observation_time",
        "quality_status": "provisional",
    }
    return row, None


def _fetch_checked(code: str, timeout: int, now: datetime, session=None) -> Tuple[Optional[dict], Optional[str]]:
    """单股查询，返回 (row, rejection_reason)。网络/解析/字段缺失统一给结构化原因。"""
    http = session or requests
    try:
        response = http.get(
            SINA_URL,
            params={"daima": f"{_prefix(code)}{str(code).zfill(6)}"},
            headers=SINA_HEADERS,
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            item = data[0] if data else None
        elif isinstance(data, dict):
            item = data
        else:
            item = None
        if not item:
            return None, "empty_response"
        return _parse_sina_flow_item(item, code, now)
    except Exception as exc:  # noqa: BLE001 单股失败原因结构化返回
        return None, f"{type(exc).__name__}"


def fetch_single_stock_fund_flow_sina(
    code: str,
    timeout: int = 8,
    session: Optional[requests.Session] = None,
    now: Optional[datetime] = None,
) -> Optional[dict]:
    """查询单只股票新浪主力资金流。返回 dict 或 None（失败/必需字段缺失）。"""
    row, _reason = _fetch_checked(code, timeout, _now_beijing(now), session=session)
    return row


def _run_fetch_batch(codes, max_workers, timeout, batch_timeout, now, worker_fn):
    """有界提交 + 单调 deadline 的并发抓取。返回 (rows, rejected)。"""
    rows: List[dict] = []
    rejected: dict = {}
    if not codes:
        return rows, rejected

    fetched_at = _now_beijing(now)
    deadline = time.monotonic() + float(batch_timeout)
    executor = ThreadPoolExecutor(max_workers=max_workers)
    pending = {}
    codes_iter = iter(codes)
    window = max(1, max_workers * 2)

    def _submit(code):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            rejected[code] = "batch_timeout"
            return None
        single_timeout = min(float(timeout), max(0.1, remaining))
        future = executor.submit(worker_fn, code, single_timeout, fetched_at)
        pending[future] = code
        return future

    try:
        for _ in range(window):
            code = next(codes_iter, None)
            if code is None:
                break
            _submit(code)

        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                for future, code in list(pending.items()):
                    rejected.setdefault(code, "batch_timeout")
                    future.cancel()
                break
            done, _not_done = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            if not done:
                for future, code in list(pending.items()):
                    rejected.setdefault(code, "batch_timeout")
                    future.cancel()
                break
            for future in done:
                code = pending.pop(future)
                try:
                    row, reason = future.result()
                except Exception as exc:  # noqa: BLE001
                    row, reason = None, f"{type(exc).__name__}"
                if row is not None:
                    rows.append(row)
                else:
                    rejected[code] = reason or "unknown_error"
                # 补一个待提交槽位（有界窗口）
                next_code = next(codes_iter, None)
                if next_code is not None:
                    _submit(next_code)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return rows, rejected


def fetch_codes_fund_flow_sina_detailed(
    codes: List[str],
    max_workers: int = 10,
    timeout: int = 8,
    batch_timeout: float = 30.0,
    now: Optional[datetime] = None,
) -> Tuple[List[dict], dict]:
    """并发查询，返回 (rows, rejected)。rejected 为 {code: reason}。"""
    return _run_fetch_batch(codes, max_workers, timeout, batch_timeout, now, _fetch_checked)


def fetch_codes_fund_flow_sina(
    codes: List[str],
    max_workers: int = 10,
    timeout: int = 8,
    batch_timeout: float = 30.0,
    now: Optional[datetime] = None,
) -> List[dict]:
    """兼容入口：并发查询多只股票，返回成功行列表。"""

    def _worker(code, single_timeout, fetched_at):
        row = fetch_single_stock_fund_flow_sina(code, timeout=single_timeout, now=fetched_at)
        return row, None

    rows, _rejected = _run_fetch_batch(codes, max_workers, timeout, batch_timeout, now, _worker)
    return rows
