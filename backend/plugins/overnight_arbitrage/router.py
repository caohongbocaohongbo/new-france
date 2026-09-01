"""尾盘隔夜套利插件 API。"""
import hmac
import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query, status

from .service import (
    read_overnight_history_resilient,
    read_overnight_report_resilient,
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
            "尾盘套利完成: status=%s BUY=%s WATCH=%s",
            result.get("status", "completed"),
            result.get("buy_count", 0),
            result.get("watch_count", 0),
        )
    except Exception as exc:
        logger.exception("尾盘套利后台任务异常: %s", exc)
        _cache_error(str(exc))


def _verify_trigger_token(provided_token: str) -> None:
    expected_token = os.environ.get("OA_TRIGGER_TOKEN", "").strip()
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OA_TRIGGER_TOKEN 未配置，正式定时入口不可用",
        )
    if not provided_token or not hmac.compare_digest(provided_token, expected_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="定时触发令牌无效")


@router.post("/run")
async def run_overnight_endpoint(
    background_tasks: BackgroundTasks,
    dry_run: bool = Query(True),
    trigger_token: str = Header("", alias="X-OA-Trigger-Token"),
):
    """手动触发默认只试跑；显式发信时必须提供定时触发令牌。"""
    if not dry_run:
        _verify_trigger_token(trigger_token)
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


@router.post("/scheduled-run", status_code=status.HTTP_202_ACCEPTED)
async def run_scheduled_overnight_endpoint(
    background_tasks: BackgroundTasks,
    trigger_token: str = Header("", alias="X-OA-Trigger-Token"),
):
    """供外部定时器调用的正式入口，只接受共享令牌鉴权。"""
    _verify_trigger_token(trigger_token)
    _write_overnight_cache({
        "status": "running",
        "strategy": "overnight_arbitrage",
        "trigger": "external_scheduler",
        "message": "外部定时任务已启动",
        "results": [],
    })
    background_tasks.add_task(_run_overnight_task, False)
    return {
        "status": "started",
        "strategy": "overnight_arbitrage",
        "trigger": "external_scheduler",
        "message": "尾盘隔夜套利正式任务已启动",
    }


@router.get("/latest")
async def get_latest_overnight():
    """读取最新尾盘隔夜套利决策。"""
    return read_overnight_report_resilient()


@router.get("/history")
async def get_overnight_history():
    """读取尾盘隔夜套利跨日推荐统计。"""
    return read_overnight_history_resilient()


# ---- 03 T+1 溢价校准（扩展，不改核心决策） ----
@router.get("/calibration/latest")
async def get_calibration_latest():
    try:
        from .calibration import read_latest

        return read_latest()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/calibration/samples")
async def get_calibration_samples(score_bucket: str = Query(None)):
    try:
        from .calibration import read_samples

        return {"status": "ok", "records": read_samples(score_bucket)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
