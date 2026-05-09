"""
筛选 API — 触发筛选、查看结果
"""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional

router = APIRouter()


@router.get("/latest")
async def get_latest_screening():
    """获取最新筛选结果（从 reports/ 目录读取最新HTML报告）"""
    from pathlib import Path
    from datetime import date
    reports_dir = Path(__file__).resolve().parent.parent.parent / "reports"
    today = date.today().strftime("%Y-%m-%d")
    html_file = reports_dir / f"{today}.html"

    if html_file.exists():
        return {"date": today, "has_report": True, "html_path": str(html_file)}
    return {"date": today, "has_report": False, "html_path": None,
            "message": "今日暂无筛选报告"}


@router.post("/run")
async def run_screening(force: bool = Query(False)):
    """手动触发筛选（异步任务）"""
    return {"status": "queued", "message": "筛选任务已加入队列"}


@router.get("/history")
async def get_history(date_from: Optional[str] = Query(None),
                      date_to: Optional[str] = Query(None),
                      page: int = Query(1, ge=1),
                      size: int = Query(20, ge=1, le=100)):
    """历史筛选结果列表"""
    from pathlib import Path
    reports_dir = Path(__file__).resolve().parent.parent.parent / "reports"
    reports = sorted(reports_dir.glob("*.html"), reverse=True)
    # 按日期过滤
    if date_from:
        reports = [r for r in reports if r.stem >= date_from]
    if date_to:
        reports = [r for r in reports if r.stem <= date_to]
    total = len(reports)
    start = (page - 1) * size
    items = [{"date": r.stem, "path": str(r)} for r in reports[start:start + size]]
    return {"total": total, "page": page, "size": size, "items": items}
