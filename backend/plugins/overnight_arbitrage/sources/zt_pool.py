"""尾盘隔夜套利插件独立涨停股池数据源。

派生说明（重要 - 维护者必读）：
- 本文件最初从 `backend/agents/layer1_data_collector/sources/eastmoney_zt.py` 派生而来
- 派生为复制式 fork，不再与主项目同步：插件迁移到新项目时整目录可直接复制
- 主项目 eastmoney_zt.py 后续修复 bug 或字段升级时，**需要人工评估是否同步回本文件**
- 反之亦然：本文件的优化不会自动回馈主项目

最近一次同步参考：与主项目 eastmoney_zt.py 在插件化时的版本一致。
"""
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
import pandas as pd

logger = logging.getLogger(__name__)

BEIJING_TZ = timezone(timedelta(hours=8))


def _parse_time_value(value) -> int:
    """把 09:25:00 / 92500 / 092500 统一为 HHMMSS 整数。"""
    if value is None or value == "":
        return 0
    text = str(value).strip()
    if ":" in text:
        parts = text.split(":")
        if len(parts) >= 2:
            try:
                hour = int(parts[0])
                minute = int(parts[1])
                second = int(parts[2]) if len(parts) > 2 else 0
                return hour * 10000 + minute * 100 + second
            except (TypeError, ValueError):
                return 0
    try:
        num = int(float(text))
    except (TypeError, ValueError):
        return 0
    if num > 999999:
        return int(str(num)[:6])
    return num


def _safe_float(value, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _with_meta(df: pd.DataFrame, meta: dict) -> pd.DataFrame:
    df.attrs["source_meta"] = meta
    return df


def _fetch_zt_pool_akshare(date_str: str) -> Optional[pd.DataFrame]:
    """AkShare 兜底源，同样封装东方财富涨停池。"""
    try:
        import akshare as ak
    except ImportError:
        logger.warning("AkShare 未安装，无法启用涨停池兜底源")
        return None

    try:
        raw = ak.stock_zt_pool_em(date=date_str)
    except Exception as e:
        logger.warning(f"AkShare 涨停股池失败: {e}")
        return None

    if raw is None or raw.empty:
        return None

    rows = []
    filtered = 0
    for _, item in raw.iterrows():
        code = str(item.get("代码", "")).strip().zfill(6)
        name = str(item.get("名称", "")).strip()
        price = _safe_float(item.get("最新价"))
        if not code or price <= 0 or price < 1 or price > 10000:
            filtered += 1
            continue
        rows.append({
            "代码": code,
            "名称": name,
            "最新价": price,
            "涨跌幅": _safe_float(item.get("涨跌幅")),
            "换手率": _safe_float(item.get("换手率")),
            "流通市值": _safe_float(item.get("流通市值")),
            "封板时间": _parse_time_value(item.get("首次封板时间") or item.get("最后封板时间")),
            "炸板次数": _safe_int(item.get("炸板次数")),
            "连板数": _safe_int(item.get("连板数")),
            "涨停统计": str(item.get("涨停统计", "")).strip(),
            "所属行业": str(item.get("所属行业", "")).strip(),
        })

    df = pd.DataFrame(rows)
    meta = {
        "source": "akshare_stock_zt_pool_em",
        "date": date_str,
        "raw_count": len(raw),
        "final_count": len(df),
        "filtered_count": filtered,
        "fetched_at": datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "note": "AkShare 封装东方财富涨停股池，作为直连接口失败时的兜底源",
    }
    logger.info(f"  AkShare 涨停股池: 原始{len(raw)}只, 有效{len(df)}只")
    return _with_meta(df, meta)


def fetch_zt_pool(target_date: Optional[datetime] = None) -> Optional[pd.DataFrame]:
    """从东方财富涨停股池获取当日涨停股票"""
    if target_date is None:
        now = datetime.now(BEIJING_TZ)
    elif isinstance(target_date, datetime):
        now = target_date.astimezone(BEIJING_TZ) if target_date.tzinfo else target_date.replace(tzinfo=BEIJING_TZ)
    else:
        now = datetime.combine(target_date, datetime.min.time(), tzinfo=BEIJING_TZ)
    date_str = now.strftime("%Y%m%d")
    url = "https://push2ex.eastmoney.com/getTopicZTPool"
    params = {
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "dpt": "wz.ztzt",
        "Pageindex": 0,
        "pagesize": 10000,
        "sort": "fbt:asc",
        "date": date_str,
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://quote.eastmoney.com/ztb/detail",
    }

    try:
        from curl_cffi import requests as curl_req
        use_curl = True
    except ImportError:
        import requests as curl_req
        use_curl = False

    for attempt in range(2):
        try:
            kwargs = dict(params=params, headers=headers, timeout=15)
            if use_curl:
                resp = curl_req.get(url, impersonate="chrome120", **kwargs)
            else:
                resp = curl_req.get(url, **kwargs)
            resp.raise_for_status()
            d = resp.json()
            pool = d.get("data", {}).get("pool", [])
            if not pool:
                if attempt == 0:
                    time.sleep(3)
                    continue
                return _fetch_zt_pool_akshare(date_str)

            rows = []
            filtered = 0
            for item in pool:
                code = str(item.get("c", "")).zfill(6)
                name = str(item.get("n", ""))
                price = float(item.get("p", 0)) / 1000  # push2ex API: p 需 /1000 得元
                if price <= 0:
                    filtered += 1
                    continue
                # 价格合理性校验：A股正常区间 1-10000 元
                if price < 1 or price > 10000:
                    logger.warning(f"  异常价格跳过: {code} {name} price={price}")
                    filtered += 1
                    continue
                fbt_val = _parse_time_value(item.get("fbt", 0))
                zbc_val = _safe_int(item.get("zbc", 0))
                rows.append({
                    "代码": code,
                    "名称": name,
                    "最新价": price,
                    "涨跌幅": float(item.get("zdp", 0)),
                    "换手率": float(item.get("hs", 0)),
                    "流通市值": float(item.get("ltsz", 0)),
                    "封板时间": fbt_val,
                    "炸板次数": zbc_val,
                    "连板数": _safe_int(item.get("lbc", 0)),
                    "涨停统计": str(item.get("zttj", "")).strip(),
                })
            df = pd.DataFrame(rows)
            meta = {
                "source": "eastmoney_direct",
                "date": date_str,
                "raw_count": len(pool),
                "final_count": len(df),
                "filtered_count": filtered,
                "fetched_at": datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                "note": "今日涨停数量为采集时点的东方财富涨停股池有效数据，不是策略筛选通过数",
            }
            logger.info(f"  涨停股池: 原始{len(pool)}只, 有效{len(df)}只")
            return _with_meta(df, meta)
        except Exception as e:
            logger.warning(f"涨停股池失败(第{attempt+1}次): {e}")
            if attempt == 0:
                time.sleep(3)
    return _fetch_zt_pool_akshare(date_str)
