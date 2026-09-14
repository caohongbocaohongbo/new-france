"""
实时行情兼容入口 — 腾讯批量优先，旧源按缺失代码补齐。
"""
import logging
import os
import time
from typing import List
from urllib.parse import quote
import pandas as pd
from .quote_contract import number

logger = logging.getLogger(__name__)

BATCH_SIZE = 60  # 初始保守批量，腾讯与兼容源共用
MIN_SPLIT_BATCH_SIZE = 10  # 大批失败后拆小批重试，降低 GitHub Actions 限流/空响应影响
BATCH_DELAY_SECONDS = 0.25
ALLOW_STALE_QUOTE_CACHE = os.getenv("ALLOW_STALE_QUOTE_CACHE", "").lower() in {"1", "true", "yes"}
_QUOTE_CACHE = {}


class QuoteSourcesUnavailable(RuntimeError):
    """整批供应商不可用，不通过递归拆分重复轰炸相同端点。"""


def _fetch_tencent_batch(secids_batch: List[str]) -> list:
    """最多60只一批，仅接受当日新鲜且必需字段完整的腾讯报价。"""
    import requests
    from .quote_contract import parse_tencent_quotes, quote_is_current

    symbols = [("sh" if secid.startswith("1.") else "sz") + secid.split(".", 1)[1]
               for secid in secids_batch]
    rows = []
    for start in range(0, len(symbols), 60):
        batch = symbols[start:start + 60]
        response = requests.get("https://qt.gtimg.cn/q=" + ",".join(batch),
                                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
                                timeout=(3, 5))
        response.raise_for_status()
        response.encoding = "gbk"
        rows.extend(row for row in parse_tencent_quotes(response.text, batch)
                    if not row["degraded"] and quote_is_current(row))
    return rows


def _fetch_one_batch(secids_batch: List[str]) -> list:
    """独立报价源优先；只对缺失代码走兼容后备，不覆盖已成功报价。"""
    try:
        rows = _fetch_tencent_batch(secids_batch)
    except Exception as exc:
        logger.debug("腾讯批量报价失败: %s", exc)
        rows = []
    found = {row["代码"] for row in rows}
    missing = [secid for secid in secids_batch if secid.split(".", 1)[1] not in found]
    if missing:
        try:
            rows.extend(_fetch_legacy_batch(missing))
        except Exception:
            if not rows:
                raise
            logger.warning("报价部分缺失，保留腾讯已成功的%d只", len(rows))
    return rows


def _fetch_legacy_batch(secids_batch: List[str]) -> list:
    """兼容后备：东财一次，再新浪一次，不叠加同源客户端重试。"""
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

    errors = []
    try:
        import requests
        resp = requests.get(url, params=params, headers=headers, timeout=(3, 5))
        resp.raise_for_status()
        wanted = {secid.split(".", 1)[1] for secid in secids_batch}
        rows = [row for row in _parse_response(resp.json()) if row["代码"] in wanted]
        if rows:
            return rows
        errors.append("东财返回空行情")
    except Exception as exc:
        errors.append(f"东财: {exc}")
    try:
        rows = _fetch_sina_batch(secids_batch)
        if rows:
            return rows
        errors.append("新浪返回空行情")
    except Exception as exc:
        errors.append(f"新浪: {exc}")
    raise QuoteSourcesUnavailable("; ".join(errors))


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
    resp = requests.get(url, headers=headers, timeout=(3, 5))
    resp.raise_for_status()
    resp.encoding = "gbk"

    rows = []
    for chunk in resp.text.split(";"):
        if not chunk.strip() or '="' not in chunk:
            continue
        symbol = chunk.split("hq_str_", 1)[-1].split("=", 1)[0]
        if symbol not in symbols:
            continue
        code = symbol[-6:]
        raw = chunk.split('="', 1)[1].rstrip('"')
        parts = raw.split(",")
        if len(parts) < 9 or not parts[0]:
            continue

        def _float_at(index):
            try:
                value = number(parts[index])
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
            "成交量": None if _float_at(8) is None else _float_at(8) / 100,
            "成交额": amount,
            "换手率": None,
            "市盈率": None,
            "量比": None,
            "总市值": None,
            "流通市值": None,
            "source": "sina", "degraded": True,
            "source_time": (parts[30] + "T" + parts[31] + "+08:00") if len(parts) > 31 else None,
            "missing_fields": ["换手率", "市盈率", "量比", "总市值", "流通市值"],
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
            return number(v)

        fields = {
            "最新价": _float("f2"), "涨跌幅": _float("f3"),
            "成交量": _float("f5"), "成交额": _float("f6"),
            "换手率": _float("f8"), "市盈率": _float("f9"),
            "量比": _float("f10"), "总市值": _float("f20"),
            "流通市值": _float("f21"),
        }
        missing_fields = [key for key in ("涨跌幅", "成交量", "成交额") if fields.get(key) is None]
        rows.append({
            "代码": code, "名称": name, **fields,
            "source": "eastmoney", "source_time": None,
            # 东财此端点无逐行行情时间：未知时间不得冒充新鲜，必须显式降级
            "degraded": True,
            "missing_fields": missing_fields,
        })
    return rows


def _fetch_batch_with_split(secids_batch: List[str], batch_no: str = "") -> tuple[list, int]:
    """
    请求一批行情；大批失败时递归拆小批重试。

    返回 (rows, failed_leaf_batches)。只返回真实接口数据，不用占位值。
    """
    try:
        return _fetch_one_batch(secids_batch), 0
    except QuoteSourcesUnavailable as exc:
        logger.warning("行情源均不可用，跳过拆分重试: %s", exc)
        return [], 1
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


def fetch_tencent_quotes_for_codes(codes: list) -> pd.DataFrame:
    """候选精查：对给定代码走腾讯批量优先 + 缺失后备，返回带质量字段的报价行。"""
    secids = [("1." if str(c).startswith(("6", "9")) else "0.") + str(c).zfill(6) for c in codes]
    if not secids:
        return pd.DataFrame()
    rows = _fetch_batch_with_split(secids)[0]
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def fetch_single_quote_verified(code: str) -> dict:
    """对单只股票做交叉验证：东方财富 + 新浪备用源对比价格"""
    result = {"code": code, "price_eastmoney": None, "price_sina": None,
              "verified": False, "discrepancy": None}

    # 东方财富源
    try:
        market = "1" if code.startswith(("6", "9")) else "0"
        df = pd.DataFrame(_fetch_legacy_batch([f"{market}.{code}"]))
        if not df.empty and df.iloc[0].get("source") == "eastmoney":
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
        cached_rows = [dict(_QUOTE_CACHE[code], source="cache", degraded=True, is_stale=True) for code in codes
                       if code not in found_codes and code in _QUOTE_CACHE]
        if cached_rows:
            all_rows.extend(cached_rows)
            logger.warning(f"  使用进程内缓存行情补齐: {len(cached_rows)} 只")

    logger.info(f"  行情获取完成: {len(all_rows)}/{len(codes)} 只"
                + (f" (失败 {failed_batches} 批)" if failed_batches else ""))
    df = pd.DataFrame(all_rows)
    found = {row["代码"] for row in all_rows}
    df.attrs["source_meta"] = {
        "sources": sorted({row.get("source", "unknown") for row in all_rows}),
        "requested_count": len(set(codes)), "received_count": len(found),
        "missing_codes": sorted(set(codes) - found),
        "degraded": bool(set(codes) - found) or any(row.get("degraded", False) for row in all_rows),
    }
    return df
