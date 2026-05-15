"""监控列表 API"""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional
import re
import os
import logging
from pathlib import Path

router = APIRouter()
logger = logging.getLogger(__name__)

FRANCE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "france.md"


def _parse_watchlist():
    """解析 france.md 监控列表"""
    if not FRANCE_FILE.exists():
        return []
    try:
        content = FRANCE_FILE.read_text(encoding="utf-8")
    except Exception:
        return []
    entries = []
    for line in content.split("\n"):
        match = re.match(
            r"\|\s*(\d{6})\s*\|\s*(.+?)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([\d.]+)\s*\|",
            line
        )
        if match:
            entries.append({
                "code": match.group(1),
                "name": match.group(2).strip(),
                "zt_date": match.group(3),
                "ref_price": float(match.group(4)),
            })
    return entries


def _enrich_with_quotes(entries: list) -> list:
    """用东方财富实时行情补全回撤%、换手率、量比、PE、流通市值"""
    if not entries:
        return entries

    codes = [e["code"] for e in entries]
    try:
        from ..agents.layer1_data_collector.sources.eastmoney_quote import fetch_stock_quotes
        quotes = fetch_stock_quotes(codes)
    except Exception as e:
        logger.warning(f"行情拉取失败: {e}")
        quotes = None

    quote_map = {}
    if quotes is not None and not quotes.empty:
        for _, row in quotes.iterrows():
            quote_map[row["代码"]] = row

    result = []
    for e in entries:
        code = e["code"]
        ref = e["ref_price"]
        q = quote_map.get(code)
        if q is not None:
            current = q.get("最新价")
            if current is not None and current > 0:
                drop_pct = round((current - ref) / ref * 100, 2)
            else:
                drop_pct = None
            entry = {
                **e,
                "current_price": current,
                "drop_pct": drop_pct,
                "turnover": q.get("换手率"),
                "vol_ratio": q.get("量比"),
                "pe": q.get("市盈率"),
                "mcap_raw": q.get("流通市值"),
                "mcap": f"{q.get('流通市值', 0) / 1e8:.0f}亿" if q.get("流通市值") and q["流通市值"] > 0 else None,
            }
        else:
            entry = {
                **e,
                "current_price": None, "drop_pct": None,
                "turnover": None, "vol_ratio": None,
                "pe": None, "mcap_raw": None, "mcap": None,
            }

        # 状态判断
        from datetime import datetime, timezone, timedelta
        BEIJING_TZ = timezone(timedelta(hours=8))
        today = datetime.now(BEIJING_TZ).date()
        try:
            zt_date = datetime.strptime(e["zt_date"], "%Y-%m-%d").date()
        except Exception:
            zt_date = today

        days_since = (today - zt_date).days
        if days_since > 30:
            entry["status"] = "expired"
        elif entry.get("drop_pct") is not None and abs(entry["drop_pct"]) >= 3:
            entry["status"] = "active"
        elif entry.get("drop_pct") is not None:
            entry["status"] = "active"
        else:
            entry["status"] = "active"

        result.append(entry)

    return result


@router.get("")
async def get_watchlist(status: Optional[str] = Query(None),
                        search: Optional[str] = Query(None),
                        page: int = Query(1, ge=1),
                        size: int = Query(20, ge=1, le=100)):
    """获取监控列表（含实时行情补全）"""
    entries = _parse_watchlist()

    # 补全实时行情
    entries = _enrich_with_quotes(entries)

    if search:
        entries = [e for e in entries
                   if search.lower() in e["code"] or search.lower() in e["name"]]

    if status:
        entries = [e for e in entries if e.get("status") == status]

    total = len(entries)
    start = (page - 1) * size
    items = entries[start:start + size]

    return {"total": total, "page": page, "size": size, "items": items}


@router.get("/stats")
async def get_watchlist_stats():
    """监控列表统计"""
    entries = _parse_watchlist()
    from datetime import datetime, timezone, timedelta
    BEIJING_TZ = timezone(timedelta(hours=8))
    today = datetime.now(BEIJING_TZ)
    cutoff = today - timedelta(days=30)
    new_today = sum(1 for e in entries if e["zt_date"] == today.strftime("%Y-%m-%d"))
    return {
        "total": len(entries),
        "new_today": new_today,
        "codes": [e["code"] for e in entries],
    }


@router.delete("/{code}")
async def remove_from_watchlist(code: str):
    """从监控列表中移除股票"""
    entries = _parse_watchlist()
    entries = [e for e in entries if e["code"] != code]
    header = (
        "# 涨停监控列表\n\n"
        "| 代码 | 名称 | 涨停日期 | 参考价 |\n"
        "|------|------|----------|--------|\n"
    )
    lines = [f"| {e['code']} | {e['name']} | {e['zt_date']} | {e['ref_price']:.2f} |"
             for e in entries]
    FRANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        FRANCE_FILE.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入文件失败: {e}")
    return {"message": f"已移除 {code}", "remaining": len(entries)}
