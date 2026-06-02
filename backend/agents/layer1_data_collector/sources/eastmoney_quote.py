"""
实时行情数据源 — 批量查询股票行情
requests 优先，curl_cffi 备选（GitHub Actions 环境 curl_cffi 易超时）
"""
import logging
import os
import time
from typing import List
from urllib.parse import quote
import pandas as pd

logger = logging.getLogger(__name__)

BATCH_SIZE = 80  # 每批最多80只，避免URL过长
MIN_SPLIT_BATCH_SIZE = 10  # 大批失败后拆小批重试，降低 GitHub Actions 限流/空响应影响
BATCH_DELAY_SECONDS = 0.25
ALLOW_STALE_QUOTE_CACHE = os.getenv("ALLOW_STALE_QUOTE_CACHE", "").lower() in {"1", "true", "yes"}
_QUOTE_CACHE = {}


def _fetch_one_batch(secids_batch: List[str]) -> list:
    """单批次请求，requests 优先，curl_cffi 备选。返回 rows 列表"""
    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    params = {
        "fields": "f2,f3,f5,f6,f8,f9,f10,f12,f14,f20,f21",
        "secids": ",".join(secids_batch),
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

    last_error = None
    for attempt in range(2):
        # ---- 方案1: 标准 requests（GitHub Actions 环境更稳定） ----
        try:
            import requests
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            resp.raise_for_status()
            rows = _parse_response(resp.json())
            if rows:
                return rows
            last_error = RuntimeError("东方财富返回空行情")
        except Exception as e:
            last_error = e
            logger.debug(f"  requests 请求失败: {e}")

        # ---- 方案2: curl_cffi 模拟 Chrome ----
        try:
            from curl_cffi import requests as curl_req
            resp = curl_req.get(url, params=params, headers=headers,
                               impersonate="chrome120", timeout=15)
            rows = _parse_response(resp.json())
            if rows:
                return rows
            last_error = RuntimeError("东方财富 curl_cffi 返回空行情")
        except Exception as e:
            last_error = e
            logger.debug(f"  curl_cffi 请求也失败: {e}")

        try:
            rows = _fetch_sina_batch(secids_batch)
            if rows:
                logger.warning(f"  东方财富行情不可用，新浪财经补齐 {len(rows)}/{len(secids_batch)} 只")
                return rows
        except Exception as e:
            last_error = e
            logger.debug(f"  新浪财经请求也失败: {e}")

        if attempt == 0:
            time.sleep(0.35)

    raise RuntimeError(f"所有请求方式均失败: {last_error}")


def _fetch_sina_batch(secids_batch: List[str]) -> list:
    """新浪财经实时行情兜底。只返回源头能提供的真实字段，PE/量比/换手率保持空值。"""
    import requests

    symbols = []
    for secid in secids_batch:
        market, code = secid.split(".", 1)
        prefix = "sh" if market == "1" else "sz"
        symbols.append(f"{prefix}{code}")
    if not symbols:
        return []

    url = "https://hq.sinajs.cn/list=" + quote(",".join(symbols), safe=",")
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    resp.encoding = "gbk"

    rows = []
    for chunk in resp.text.split(";"):
        if not chunk.strip() or '="' not in chunk:
            continue
        symbol = chunk.split("hq_str_", 1)[-1].split("=", 1)[0]
        code = symbol[-6:]
        raw = chunk.split('="', 1)[1].rstrip('"')
        parts = raw.split(",")
        if len(parts) < 9 or not parts[0]:
            continue

        def _float_at(index):
            try:
                value = float(parts[index])
            except (IndexError, TypeError, ValueError):
                return None
            return value

        prev_close = _float_at(2)
        latest = _float_at(3)
        amount = _float_at(9)
        change_pct = None
        if prev_close and latest is not None:
            change_pct = (latest - prev_close) / prev_close * 100

        rows.append({
            "代码": code,
            "名称": parts[0],
            "最新价": latest,
            "涨跌幅": change_pct,
            "成交量": _float_at(8),
            "成交额": amount,
            "换手率": None,
            "市盈率": None,
            "量比": None,
            "总市值": None,
            "流通市值": None,
        })
    return rows


def _parse_response(data: dict) -> list:
    """解析东方财富 API 响应"""
    if not isinstance(data, dict):
        return []
    payload = data.get("data")
    if not isinstance(payload, dict):
        return []
    items = payload.get("diff")
    if not items:
        return []

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
    return rows


def _fetch_batch_with_split(secids_batch: List[str], batch_no: str = "") -> tuple[list, int]:
    """
    请求一批行情；大批失败时递归拆小批重试。

    返回 (rows, failed_leaf_batches)。只返回真实接口数据，不用占位值。
    """
    try:
        return _fetch_one_batch(secids_batch), 0
    except Exception as e:
        if len(secids_batch) <= MIN_SPLIT_BATCH_SIZE:
            label = f" {batch_no}" if batch_no else ""
            logger.warning(f"  行情批次{label}失败: {e}")
            return [], 1

        midpoint = len(secids_batch) // 2
        label = f" {batch_no}" if batch_no else ""
        logger.warning(
            f"  行情批次{label}失败，拆分为 {midpoint}/{len(secids_batch) - midpoint} 小批重试: {e}"
        )
        rows = []
        failed = 0
        left_rows, left_failed = _fetch_batch_with_split(secids_batch[:midpoint], f"{batch_no}.1" if batch_no else "1")
        rows.extend(left_rows)
        failed += left_failed
        if BATCH_DELAY_SECONDS > 0:
            time.sleep(BATCH_DELAY_SECONDS)
        right_rows, right_failed = _fetch_batch_with_split(secids_batch[midpoint:], f"{batch_no}.2" if batch_no else "2")
        rows.extend(right_rows)
        failed += right_failed
        return rows, failed


def fetch_single_quote_verified(code: str) -> dict:
    """对单只股票做交叉验证：东方财富 + 新浪备用源对比价格"""
    result = {"code": code, "price_eastmoney": None, "price_sina": None,
              "verified": False, "discrepancy": None}

    # 东方财富源
    try:
        df = fetch_stock_quotes([code])
        if not df.empty:
            result["price_eastmoney"] = float(df.iloc[0]["最新价"])
    except Exception:
        pass

    # 新浪备用源
    try:
        import requests
        prefix = "sh" if code.startswith(("6", "9")) else "sz"
        url = "https://hq.sinajs.cn/list=" + prefix + code
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = "gbk"
        parts = resp.text.split(",")
        if len(parts) > 3:
            result["price_sina"] = float(parts[3])
    except Exception:
        pass

    # 交叉验证
    if result["price_eastmoney"] and result["price_sina"]:
        diff = abs(result["price_eastmoney"] - result["price_sina"])
        pct = diff / result["price_eastmoney"] * 100
        result["discrepancy"] = round(pct, 4)
        result["verified"] = pct < 1.0  # 差异 < 1% 视为验证通过

    return result


def fetch_stock_quotes(codes: List[str]) -> pd.DataFrame:
    """批量查询指定股票的实时行情（自动分批，单批失败不影响其他批）"""
    if not codes:
        return pd.DataFrame()

    all_secids = []
    for code in codes:
        market = "1" if code.startswith(("6", "9")) else "0"
        all_secids.append(f"{market}.{code}")

    all_rows = []
    failed_batches = 0

    for i in range(0, len(all_secids), BATCH_SIZE):
        batch = all_secids[i:i + BATCH_SIZE]
        batch_rows, failed_leaf_batches = _fetch_batch_with_split(batch, str(i // BATCH_SIZE + 1))
        failed_batches += failed_leaf_batches
        all_rows.extend(batch_rows)
        for row in batch_rows:
            _QUOTE_CACHE[row["代码"]] = row
        if BATCH_DELAY_SECONDS > 0:
            time.sleep(BATCH_DELAY_SECONDS)

    if ALLOW_STALE_QUOTE_CACHE:
        found_codes = {row["代码"] for row in all_rows}
        cached_rows = [_QUOTE_CACHE[code] for code in codes
                       if code not in found_codes and code in _QUOTE_CACHE]
        if cached_rows:
            all_rows.extend(cached_rows)
            logger.warning(f"  使用进程内缓存行情补齐: {len(cached_rows)} 只")

    logger.info(f"  行情获取完成: {len(all_rows)}/{len(codes)} 只"
                + (f" (失败 {failed_batches} 批)" if failed_batches else ""))
    return pd.DataFrame(all_rows)
