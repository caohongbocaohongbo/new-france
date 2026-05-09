"""监控列表 API"""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional
import re
import os
from pathlib import Path

router = APIRouter()

FRANCE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "france.md"


def _parse_watchlist():
    """解析 france.md 监控列表"""
    if not FRANCE_FILE.exists():
        return []
    content = FRANCE_FILE.read_text(encoding="utf-8")
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


@router.get("")
async def get_watchlist(status: Optional[str] = Query(None),
                        search: Optional[str] = Query(None),
                        page: int = Query(1, ge=1),
                        size: int = Query(20, ge=1, le=100)):
    """获取监控列表"""
    entries = _parse_watchlist()

    if search:
        entries = [e for e in entries
                   if search.lower() in e["code"] or search.lower() in e["name"]]

    total = len(entries)
    start = (page - 1) * size
    items = entries[start:start + size]

    return {"total": total, "page": page, "size": size, "items": items}


@router.get("/stats")
async def get_watchlist_stats():
    """监控列表统计"""
    entries = _parse_watchlist()
    from datetime import datetime, timedelta
    today = datetime.now()
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
    # 重写文件
    header = (
        "# 涨停监控列表\n\n"
        "| 代码 | 名称 | 涨停日期 | 参考价 |\n"
        "|------|------|----------|--------|\n"
    )
    lines = [f"| {e['code']} | {e['name']} | {e['zt_date']} | {e['ref_price']:.2f} |"
             for e in entries]
    FRANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    FRANCE_FILE.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return {"message": f"已移除 {code}", "remaining": len(entries)}
