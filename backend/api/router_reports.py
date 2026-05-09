"""报告 API"""
from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, FileResponse
from pathlib import Path
from typing import Optional
from datetime import date

router = APIRouter()
REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "reports"


@router.get("/latest")
async def get_latest_report():
    """最新报告"""
    today = date.today().strftime("%Y-%m-%d")
    html_file = REPORTS_DIR / f"{today}.html"

    if html_file.exists():
        return {"date": today, "has_report": True,
                "url": f"/api/v1/reports/{today}"}
    return {"date": today, "has_report": False,
            "message": "今日暂无报告"}


@router.get("/list")
async def list_reports(page: int = Query(1, ge=1),
                       size: int = Query(20, ge=1, le=100)):
    """报告列表"""
    reports = sorted(REPORTS_DIR.glob("*.html"), reverse=True)
    total = len(reports)
    start = (page - 1) * size
    items = [{"date": r.stem} for r in reports[start:start + size]]
    return {"total": total, "page": page, "items": items}


@router.get("/{target_date}", response_class=HTMLResponse)
async def get_report(target_date: str):
    """获取指定日期 HTML 报告"""
    html_file = REPORTS_DIR / f"{target_date}.html"
    if not html_file.exists():
        return HTMLResponse(
            "<h2 style='color:#8B95A8;text-align:center;margin-top:100px'>"
            "该日期暂无报告</h2>", status_code=404)
    return HTMLResponse(html_file.read_text(encoding="utf-8"))


@router.get("/{target_date}/download")
async def download_report(target_date: str):
    """下载指定日期 Markdown 报告"""
    md_file = REPORTS_DIR / f"{target_date}.md"
    if md_file.exists():
        return FileResponse(md_file, filename=f"new-france-{target_date}.md")
    return {"error": "报告不存在"}, 404
