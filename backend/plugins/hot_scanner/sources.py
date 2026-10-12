"""26 舆情热度聚合：数据源抓取（尽力而为，单源失败不影响其他源）。

注意：外部站点 URL/反爬策略可能变化，接入时需国内 IP 实测；
每个源独立 try/except + timeout，失败返回空列表。
"""
import logging

import requests

from .config import TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}


def _get(url, timeout=None, **kwargs):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout or TIMEOUT_SECONDS, **kwargs)
        resp.raise_for_status()
        return resp
    except Exception as exc:  # noqa: BLE001
        logger.info("舆情源抓取失败 %s: %s", url, exc)
        return None


def eastmoney_rank() -> list:
    """东财人气榜：返回 [{code,name,score}]。"""
    try:
        url = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
        resp = requests.post(url, json={"appId": "appId01", "globalId": "786e4c21-70dc-435a-93bb-38", "marketType": "", "pageNo": 1, "pageSize": 100}, headers=HEADERS, timeout=TIMEOUT_SECONDS)
        if resp.status_code != 200:
            return []
        data = resp.json().get("data") or []
        out = []
        for i, item in enumerate(data, 1):
            code = str(item.get("sc", "")).zfill(6)
            if not code:
                continue
            score = 3 if i <= 10 else (2 if i <= 50 else 1)
            out.append({"code": code, "name": item.get("sn", ""), "score": score, "source": "eastmoney_rank"})
        return out
    except Exception as exc:  # noqa: BLE001
        logger.info("东财人气榜抓取失败: %s", exc)
        return []


def xueqiu_hot() -> list:
    """雪球热帖：返回 [{code,name,score}]。"""
    try:
        url = "https://xueqiu.com/statuses/hot/listV2.json"
        resp = _get(url)
        if resp is None:
            return []
        data = resp.json().get("list", []) if isinstance(resp.json(), dict) else []
        out = []
        for item in data:
            text = str(item.get("text") or item.get("description") or "")
            # 只提取 A 股 6 位代码
            import re
            for code in set(re.findall(r"\b(\d{6})\b", text)):
                if code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
                    out.append({"code": code, "name": "", "score": 1, "source": "xueqiu_hot"})
        return out
    except Exception as exc:  # noqa: BLE001
        logger.info("雪球热帖抓取失败: %s", exc)
        return []


def guba_hot() -> list:
    """股吧热度：返回 [{code,name,score}]。"""
    try:
        # 东财股吧人气榜接口（候选，需实测）
        url = "https://gbapi.eastmoney.com/webarticlelist/api/Article/Articlelist"
        resp = _get(url)
        if resp is None:
            return []
        data = resp.json().get("result", {}).get("data", []) if isinstance(resp.json(), dict) else []
        out = []
        for item in data:
            code = str(item.get("post_guba", {}).get("stockbar_code", "")).zfill(6)
            if code and code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
                out.append({"code": code, "name": "", "score": 1, "source": "guba_hot"})
        return out
    except Exception as exc:  # noqa: BLE001
        logger.info("股吧热度抓取失败: %s", exc)
        return []


def ths_rank() -> list:
    """同花顺热度榜：返回 [{code,name,score}]。"""
    try:
        url = "https://eq.10jqka.com.cn/frontend/stock/rank?type=hot"
        resp = _get(url)
        if resp is None:
            return []
        out = []
        # 同花顺页面多为 JS 渲染，这里做结构化兜底：文本里匹配 6 位代码
        import re
        for code in set(re.findall(r"\b(\d{6})\b", resp.text)):
            if code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
                out.append({"code": code, "name": "", "score": 1, "source": "ths_rank"})
        return out[:100]
    except Exception as exc:  # noqa: BLE001
        logger.info("同花顺热度抓取失败: %s", exc)
        return []


ALL_SOURCES = {"eastmoney_rank": eastmoney_rank, "xueqiu_hot": xueqiu_hot,
               "guba_hot": guba_hot, "ths_rank": ths_rank}
