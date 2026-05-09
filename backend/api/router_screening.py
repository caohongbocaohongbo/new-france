"""
筛选 API — 触发筛选、查看结果
"""
import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query, HTTPException

router = APIRouter()
logger = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "reports"


@router.post("/run")
async def run_screening(
    drop_min: float = Query(3.0, ge=0, le=20),
    drop_max: float = Query(10.0, ge=0, le=20),
    vol_min: float = Query(1.0, ge=0),
    vol_max: float = Query(5.0, ge=0),
    turnover_min: float = Query(5.0, ge=0, le=50),
    turnover_max: float = Query(10.0, ge=0, le=50),
    mc_min: float = Query(50.0, ge=0),
    mc_max: float = Query(200.0, ge=0),
    pe_max: float = Query(50.0, ge=0),
):
    """手动触发筛选 — 完整三级 Agent 流水线"""
    try:
        from ..services.screening_service import run_full_pipeline

        result = await run_full_pipeline(
            target_date=date.today(),
            drop_min=drop_min, drop_max=drop_max,
            vol_min=vol_min, vol_max=vol_max,
            turnover_min=turnover_min, turnover_max=turnover_max,
            mc_min=mc_min, mc_max=mc_max,
            pe_max=pe_max,
        )

        # 缓存最新结果到 reports/latest.json
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = REPORTS_DIR / "latest.json"
        cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                              encoding="utf-8")

        return {"status": "completed", **result}

    except Exception as e:
        logger.exception(f"筛选流水线异常: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/latest")
async def get_latest_screening():
    """获取最新筛选结果"""
    cache_file = REPORTS_DIR / "latest.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            return data
        except Exception:
            pass

    html_file = REPORTS_DIR / f"{date.today().strftime('%Y-%m-%d')}.html"
    if html_file.exists():
        return {"date": date.today().strftime("%Y-%m-%d"), "has_report": True,
                "html_path": str(html_file)}
    return {"date": date.today().strftime("%Y-%m-%d"), "has_report": False,
            "results": [], "message": "今日暂无筛选报告，请先执行筛选"}


@router.get("/history")
async def get_history(date_from: Optional[str] = Query(None),
                      date_to: Optional[str] = Query(None),
                      page: int = Query(1, ge=1),
                      size: int = Query(20, ge=1, le=100)):
    """历史筛选结果列表"""
    reports = sorted(REPORTS_DIR.glob("*.md"), reverse=True)
    if date_from:
        reports = [r for r in reports if r.stem >= date_from]
    if date_to:
        reports = [r for r in reports if r.stem <= date_to]
    total = len(reports)
    start = (page - 1) * size
    items = [{"date": r.stem} for r in reports[start:start + size]]
    return {"total": total, "page": page, "size": size, "items": items}
