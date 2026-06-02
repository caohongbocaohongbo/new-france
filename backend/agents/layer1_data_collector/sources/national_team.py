"""国家队持仓数据源 — 东方财富股东分析。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

BEIJING_TZ = timezone(timedelta(hours=8))
EASTMONEY_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
SOURCE_NAME = "东方财富股东分析"
SOURCE_URL = "https://data.eastmoney.com/gdfx/HoldingAnalyse.html"

ENTITIES = [
    {
        "key": "huijin",
        "name": "中央汇金",
        "full_name": "中央汇金投资有限责任公司",
        "english_name": "Central Huijin Investment Ltd.",
        "holder_names": ["中央汇金资产管理有限责任公司", "中央汇金投资有限责任公司"],
        "keywords": ["中央汇金"],
    },
    {
        "key": "csf",
        "name": "证金公司",
        "full_name": "中国证券金融股份有限公司",
        "english_name": "",
        "holder_names": ["中国证券金融股份有限公司"],
        "keywords": ["中国证券金融", "证金"],
    },
    {
        "key": "social_security",
        "name": "社保基金",
        "full_name": "全国社会保障基金理事会",
        "english_name": "National Council for Social Security Fund",
        "holder_type_free": "全国社保基金",
        "holder_type_top": "社保",
        "keywords": ["全国社保基金", "社保基金"],
    },
]


def beijing_now() -> datetime:
    return datetime.now(BEIJING_TZ)


def entity_definitions() -> List[Dict[str, Any]]:
    return [dict(item) for item in ENTITIES]


def match_entity(holder_name: str) -> Optional[Tuple[str, str]]:
    text = str(holder_name or "")
    if not text:
        return None
    for entity in ENTITIES:
        for keyword in entity.get("keywords", []):
            if keyword in text:
                return entity["key"], entity["name"]
        for name in entity.get("holder_names", []):
            if name == text:
                return entity["key"], entity["name"]
    return None


def _as_float(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _as_int(value) -> Optional[int]:
    number = _as_float(value)
    return int(number) if number is not None else None


def _first_float(row: Dict[str, Any], keys: Iterable[str]) -> Optional[float]:
    for key in keys:
        value = _as_float(row.get(key))
        if value is not None:
            return value
    return None


def _date(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return text[:10]


def normalize_period(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text[:10]
    if "-" in text:
        return text
    if len(text) >= 8:
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


def period_for_filter(period: str) -> str:
    return normalize_period(period)


def normalize_holder_row(row: Dict[str, Any], holding_type: str, source_url: str = SOURCE_URL) -> Optional[Dict[str, Any]]:
    holder_name = str(row.get("HOLDER_NAME") or "").strip()
    matched = match_entity(holder_name)
    if not matched:
        return None
    entity_key, entity_name = matched
    is_free = holding_type == "free_float_top10"
    ratio_key = "FREE_HOLDNUM_RATIO" if is_free else "HOLD_RATIO"
    rank_key = "HOLDER_RANK" if is_free else "RANK"
    notice_key = "UPDATE_DATE" if is_free else "NOTICE_DATE"
    holder_type = row.get("HOLDER_TYPE") if is_free else row.get("HOLDER_NEWTYPE")

    return {
        "entity_key": entity_key,
        "entity_name": entity_name,
        "shareholder_name": holder_name,
        "shareholder_type": str(holder_type or "").strip(),
        "shares_type": str(row.get("SHARES_TYPE") or "").strip(),
        "holder_rank": _as_int(row.get(rank_key)),
        "stock_code": str(row.get("SECURITY_CODE") or "").strip().zfill(6),
        "stock_name": str(row.get("SECURITY_NAME_ABBR") or "").strip(),
        "report_period": _date(row.get("END_DATE")),
        "notice_date": _date(row.get(notice_key)),
        "shares": _as_float(row.get("HOLD_NUM")),
        "share_ratio": _as_float(row.get(ratio_key)),
        "shares_change": _first_float(row, ["HOLD_NUM_CHANGE", "XZCHANGE"]),
        "change_ratio": _first_float(row, ["HOLDNUM_CHANGE_RATIO", "CHANGE_RATIO", "HOLD_RATIO_CHANGE"]),
        "change_name": str(row.get("HOLDNUM_CHANGE_NAME") or "").strip(),
        "market_value": _as_float(row.get("HOLDER_MARKET_CAP")),
        "holding_type": holding_type,
        "holding_type_name": "十大流通股东" if is_free else "十大股东",
        "source": SOURCE_NAME,
        "source_url": source_url,
        "fetched_at": beijing_now().isoformat(),
    }


def _eastmoney_get(report_name: str,
                   filter_expr: Optional[str] = None,
                   page: int = 1,
                   page_size: int = 100,
                   sort_columns: Optional[str] = None,
                   sort_types: str = "-1,1,1") -> Dict[str, Any]:
    sort_columns = sort_columns or (
        "UPDATE_DATE,SECURITY_CODE,HOLDER_RANK"
        if report_name == "RPT_F10_EH_FREEHOLDERS"
        else "NOTICE_DATE,SECURITY_CODE,RANK"
    )
    params = {
        "sortColumns": sort_columns,
        "sortTypes": sort_types,
        "pageSize": str(page_size),
        "pageNumber": str(page),
        "reportName": report_name,
        "columns": "ALL",
        "source": "WEB",
        "client": "WEB",
    }
    if filter_expr:
        params["filter"] = filter_expr
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://data.eastmoney.com/gdfx/HoldingAnalyse.html",
    }
    resp = requests.get(EASTMONEY_URL, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _rows_from_payload(payload: Dict[str, Any]) -> tuple[List[Dict[str, Any]], int, int]:
    result = payload.get("result") or {}
    rows = result.get("data") or []
    return rows, int(result.get("pages") or 0), int(result.get("count") or len(rows))


def fetch_latest_periods(limit: int = 4) -> List[str]:
    payload = _eastmoney_get(
        "RPT_F10_EH_FREEHOLDERS",
        page_size=20,
        sort_columns="END_DATE",
        sort_types="-1",
    )
    rows, _, _ = _rows_from_payload(payload)
    periods = []
    seen = set()
    for row in rows:
        period = _date(row.get("END_DATE"))
        if period and period not in seen:
            periods.append(period)
            seen.add(period)
        if len(periods) >= limit:
            break
    return periods


def _filters_for_entity(entity: Dict[str, Any], holding_type: str, period: Optional[str]) -> Iterable[str]:
    date_filter = f"(END_DATE='{period_for_filter(period)}')" if period else ""
    if entity["key"] == "social_security":
        type_value = entity["holder_type_free"] if holding_type == "free_float_top10" else entity["holder_type_top"]
        type_field = "HOLDER_TYPE" if holding_type == "free_float_top10" else "HOLDER_NEWTYPE"
        yield f"{date_filter}({type_field}=\"{type_value}\")"
        return

    for holder_name in entity.get("holder_names", []):
        yield f"{date_filter}(HOLDER_NAME=\"{holder_name}\")"


def _fetch_by_filters(report_name: str,
                      holding_type: str,
                      filters: Iterable[str],
                      max_pages: int = 2,
                      page_size: int = 100) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    normalized = []
    raw_count = 0
    errors = []
    pages_seen = 0
    for filter_expr in filters:
        try:
            first = _eastmoney_get(report_name, filter_expr=filter_expr, page=1, page_size=page_size)
            rows, pages, count = _rows_from_payload(first)
            raw_count += count
            pages_seen += pages
            for row in rows:
                item = normalize_holder_row(row, holding_type=holding_type)
                if item:
                    normalized.append(item)
            for page in range(2, min(pages, max_pages) + 1):
                payload = _eastmoney_get(report_name, filter_expr=filter_expr, page=page, page_size=page_size)
                rows, _, _ = _rows_from_payload(payload)
                for row in rows:
                    item = normalize_holder_row(row, holding_type=holding_type)
                    if item:
                        normalized.append(item)
        except Exception as exc:
            logger.warning("国家队股东接口失败: %s %s", filter_expr, exc)
            errors.append(f"{filter_expr}: {exc}")
    meta = {
        "source": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "raw_count": raw_count,
        "matched_count": len(normalized),
        "pages_seen": pages_seen,
        "errors": errors,
        "fetched_at": beijing_now().isoformat(),
    }
    return normalized, meta


def fetch_holdings(periods: Optional[List[str]] = None,
                   max_pages_per_filter: int = 1,
                   page_size: int = 30) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    query_periods: List[Optional[str]]
    if periods:
        query_periods = periods
        meta_periods = periods
    else:
        query_periods = [None]
        meta_periods = ["latest"]
    holdings: List[Dict[str, Any]] = []
    metas = []
    for period in query_periods:
        for entity in ENTITIES:
            free_items, free_meta = _fetch_by_filters(
                "RPT_F10_EH_FREEHOLDERS",
                "free_float_top10",
                _filters_for_entity(entity, "free_float_top10", period),
                max_pages=max_pages_per_filter,
                page_size=page_size,
            )
            top_items, top_meta = _fetch_by_filters(
                "RPT_DMSK_HOLDERS",
                "top10",
                _filters_for_entity(entity, "top10", period),
                max_pages=max_pages_per_filter,
                page_size=page_size,
            )
            holdings.extend(free_items)
            holdings.extend(top_items)
            metas.extend([free_meta, top_meta])

    unique = {}
    for item in holdings:
        key = (
            item["entity_key"],
            item["stock_code"],
            item["shareholder_name"],
            item["report_period"],
            item["holding_type"],
        )
        unique[key] = item

    meta = {
        "source": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "periods": meta_periods,
        "raw_count": sum(m.get("raw_count", 0) for m in metas),
        "matched_count": len(unique),
        "errors": [err for m in metas for err in m.get("errors", [])],
        "fetched_at": beijing_now().isoformat(),
        "note": "证金/汇金按精确股东名过滤，社保基金按东方财富股东类型过滤",
    }
    return list(unique.values()), meta
