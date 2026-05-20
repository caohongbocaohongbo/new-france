"""
涨停股池数据源 — 从东方财富 API 拉取当日涨停板数据
"""
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
import pandas as pd

logger = logging.getLogger(__name__)

BEIJING_TZ = timezone(timedelta(hours=8))


def fetch_zt_pool() -> Optional[pd.DataFrame]:
    """从东方财富涨停股池获取当日涨停股票"""
    url = "https://push2ex.eastmoney.com/getTopicZTPool"
    params = {
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "dpt": "wz.ztzt",
        "Pageindex": 0,
        "pagesize": 500,
        "sort": "fbt:asc",
        "date": datetime.now(BEIJING_TZ).strftime("%Y%m%d"),
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
                return None

            rows = []
            for item in pool:
                code = str(item.get("c", "")).zfill(6)
                name = str(item.get("n", ""))
                price = float(item.get("p", 0)) / 1000  # push2ex API: p 需 /1000 得元
                if price <= 0:
                    continue
                # 价格合理性校验：A股正常区间 1-10000 元
                if price < 1 or price > 10000:
                    logger.warning(f"  异常价格跳过: {code} {name} price={price}")
                    continue
                fbt_val = int(item.get("fbt", 0))
                zbc_val = int(item.get("zbc", 0))
                rows.append({
                    "代码": code,
                    "名称": name,
                    "最新价": price,
                    "涨跌幅": float(item.get("zdp", 0)),
                    "换手率": float(item.get("hs", 0)),
                    "流通市值": float(item.get("ltsz", 0)),
                    "封板时间": fbt_val,
                    "炸板次数": zbc_val,
                    "连板数": int(item.get("lbc", 0)),
                })
            df = pd.DataFrame(rows)
            logger.info(f"  涨停股池: {len(df)} 只")
            return df
        except Exception as e:
            logger.warning(f"涨停股池失败(第{attempt+1}次): {e}")
            if attempt == 0:
                time.sleep(3)
    return None
