"""尾盘隔夜套利 API。"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Query

from ..services.overnight_arbitrage_service import (
    read_overnight_report,
    run_overnight_arbitrage,
    write_overnight_report,
)

router = APIRouter()
logger = logging.getLogger(__name__)
BEIJING_TZ = timezone(timedelta(hours=8))


def _write_overnight_cache(payload: dict):
    write_overnight_report(payload)


def _cache_error(msg: str):
    _write_overnight_cache({
        "status": "error",
        "strategy": "overnight_arbitrage",
        "message": msg,
        "results": [],
    })


def _execute_overnight_pipeline(dry_run: bool = False) -> dict:
    import asyncio

    target_date = datetime.now(BEIJING_TZ).date()
    return asyncio.run(run_overnight_arbitrage(target_date=target_date, dry_run=dry_run))


def _run_overnight_task(dry_run: bool = False):
    """后台执行尾盘套利任务，避免同步行情请求阻塞轮询接口。"""
    try:
        result = _execute_overnight_pipeline(dry_run=dry_run)
        _write_overnight_cache({"status": "completed", **result})
        logger.info(
            "尾盘套利完成: BUY=%s WATCH=%s",
            result.get("buy_count", 0),
            result.get("watch_count", 0),
        )
    except Exception as exc:
        logger.exception("尾盘套利后台任务异常: %s", exc)
        _cache_error(str(exc))


@router.post("/run")
async def run_overnight_endpoint(
    background_tasks: BackgroundTasks,
    dry_run: bool = Query(False),
):
    """手动触发尾盘隔夜套利，前端轮询 /latest 获取 14:43 决策。"""
    _write_overnight_cache({
        "status": "running",
        "strategy": "overnight_arbitrage",
        "message": "尾盘隔夜套利任务已启动",
        "results": [],
    })
    background_tasks.add_task(_run_overnight_task, dry_run)
    return {
        "status": "started",
        "strategy": "overnight_arbitrage",
        "message": "尾盘隔夜套利任务已启动，请轮询 /api/v1/overnight-arbitrage/latest 查看结果",
    }


@router.get("/latest")
async def get_latest_overnight():
    """读取最新尾盘隔夜套利决策。"""
    return read_overnight_report()
