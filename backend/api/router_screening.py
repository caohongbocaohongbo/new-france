"""
筛选 API — 触发筛选、查看结果
"""
import json
import logging
import asyncio
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query, HTTPException, BackgroundTasks

router = APIRouter()
logger = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "reports"


def _cache_error(msg: str):
    """缓存错误信息到 latest.json，供前端轮询"""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = REPORTS_DIR / "latest.json"
    cache_file.write_text(json.dumps({"status": "error", "message": msg}, ensure_ascii=False),
                          encoding="utf-8")


async def _run_screening_task(params: dict):
    """后台执行筛选流水线（不阻塞 HTTP 响应）"""
    try:
        from ..services.screening_service import run_full_pipeline

        result = await run_full_pipeline(
            target_date=date.today(),
            drop_min=params["drop_min"], drop_max=params["drop_max"],
            vol_min=params["vol_min"], vol_max=params["vol_max"],
            turnover_min=params["turnover_min"], turnover_max=params["turnover_max"],
            mc_min=params["mc_min"], mc_max=params["mc_max"],
            pe_max=params["pe_max"],
        )

        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = REPORTS_DIR / "latest.json"
        cache_file.write_text(json.dumps({"status": "completed", **result}, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        logger.info(f"后台筛选完成: STRONG_BUY={result['strong_buy']}, BUY={result['buy']}")

    except Exception as e:
        logger.exception(f"后台筛选异常: {e}")
        _cache_error(str(e))


@router.post("/run")
async def run_screening(
    background_tasks: BackgroundTasks,
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
    """手动触发筛选 — 后台异步执行，前端轮询 /latest 获取结果"""
    params = {
        "drop_min": drop_min, "drop_max": drop_max,
        "vol_min": vol_min, "vol_max": vol_max,
        "turnover_min": turnover_min, "turnover_max": turnover_max,
        "mc_min": mc_min, "mc_max": mc_max,
        "pe_max": pe_max,
    }

    # 写入运行中状态
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = REPORTS_DIR / "latest.json"
    cache_file.write_text(json.dumps({"status": "running", "params": params}, ensure_ascii=False),
                          encoding="utf-8")

    # 后台执行（不阻塞响应，避免 Render 30s 网关超时）
    background_tasks.add_task(_run_screening_task, params)

    return {
        "status": "started",
        "message": "筛选任务已启动，请轮询 /api/v1/screening/latest 查看结果",
    }


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
