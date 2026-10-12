"""26 事件驱动雷达：数据源抓取（尽力而为，单源失败不影响其他源）。"""
import logging

import requests

from .config import TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"}


def fetch_announcements() -> list:
    """东财公告接口（候选，需国内 IP 实测）。返回 [{code,name,title,date,url}]。"""
    try:
        url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
        resp = requests.post(url, json={"page_size": 50, "page_number": 1, "ann_type": "A", "client_source": "web"},
                             headers=HEADERS, timeout=TIMEOUT_SECONDS)
        if resp.status_code != 200:
            return []
        data = resp.json().get("data", {}).get("list", [])
        out = []
        for item in data:
            code = str(item.get("codes", [{}])[0].get("stock_code", "")).zfill(6) if item.get("codes") else ""
            out.append({"code": code, "name": item.get("short_name", ""),
                        "title": item.get("title", ""), "date": str(item.get("notice_date", ""))[:10],
                        "url": item.get("art_code", "")})
        return out
    except Exception as exc:  # noqa: BLE001
        logger.info("公告抓取失败: %s", exc)
        return []


def fetch_reports() -> list:
    """东财研报接口（候选，需实测）。返回 [{code,name,title,date,url}]。"""
    try:
        url = "https://reportapi.eastmoney.com/report/list"
        resp = requests.get(url, params={"industryCode": "*", "pageSize": 50, "pageNo": 1,
                                          "qType": 0, "beginTime": "", "endTime": ""},
                            headers=HEADERS, timeout=TIMEOUT_SECONDS)
        if resp.status_code != 200:
            return []
        data = resp.json().get("data", [])
        out = []
        for item in data:
            out.append({"code": str(item.get("stockCode", "")).zfill(6), "name": item.get("stockName", ""),
                        "title": item.get("title", ""), "date": str(item.get("publishDate", ""))[:10],
                        "url": item.get("encodeUrl", "")})
        return out
    except Exception as exc:  # noqa: BLE001
        logger.info("研报抓取失败: %s", exc)
        return []


def fetch_holder_changes() -> list:
    """大股东增减持（候选，需实测）。返回 [{code,name,title,date,url}]。"""
    try:
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {"reportName": "RPT_HOLDER_SHARE_CHANGE", "columns": "ALL",
                  "pageSize": 50, "pageNumber": 1, "sortTypes": "-1", "sortColumns": "CHANGE_DATE"}
        resp = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT_SECONDS)
        if resp.status_code != 200:
            return []
        data = resp.json().get("result", {}).get("data", [])
        out = []
        for item in data:
            code = str(item.get("SECURITY_CODE", "")).zfill(6)
            out.append({"code": code, "name": item.get("SECURITY_NAME_ABBR", ""),
                        "title": str(item.get("CHANGE_REASON", "股东增减持")),
                        "date": str(item.get("CHANGE_DATE", ""))[:10], "url": ""})
        return out
    except Exception as exc:  # noqa: BLE001
        logger.info("增减持抓取失败: %s", exc)
        return []


ALL_SOURCES = {"公告": fetch_announcements, "研报": fetch_reports, "增减持": fetch_holder_changes}
