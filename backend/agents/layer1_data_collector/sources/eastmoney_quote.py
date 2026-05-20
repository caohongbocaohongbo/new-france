"""
实时行情数据源 — 批量查询股票行情
requests 优先，curl_cffi 备选（GitHub Actions 环境 curl_cffi 易超时）
"""
import logging
from typing import List
import pandas as pd

logger = logging.getLogger(__name__)

BATCH_SIZE = 80  # 每批最多80只，避免URL过长


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

    # ---- 方案1: 标准 requests（GitHub Actions 环境更稳定） ----
    try:
        import requests
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        return _parse_response(resp.json())
    except Exception as e:
        logger.debug(f"  requests 请求失败: {e}")

    # ---- 方案2: curl_cffi 模拟 Chrome ----
    try:
        from curl_cffi import requests as curl_req
        resp = curl_req.get(url, params=params, headers=headers,
                           impersonate="chrome120", timeout=15)
        return _parse_response(resp.json())
    except Exception as e:
        logger.debug(f"  curl_cffi 请求也失败: {e}")

    raise RuntimeError("所有请求方式均失败")


def _parse_response(data: dict) -> list:
    """解析东方财富 API 响应"""
    items = data.get("data", {}).get("diff")
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
        try:
            batch_rows = _fetch_one_batch(batch)
            all_rows.extend(batch_rows)
        except Exception as e:
            failed_batches += 1
            logger.warning(f"  行情批次 {i // BATCH_SIZE + 1} 失败: {e}")

    logger.info(f"  行情获取完成: {len(all_rows)}/{len(codes)} 只"
                + (f" (失败 {failed_batches} 批)" if failed_batches else ""))
    return pd.DataFrame(all_rows)
