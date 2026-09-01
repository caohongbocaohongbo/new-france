"""主力资金双向监控 API（plugin 独立路由）。

挂载点: /api/v1/principal-capital (由 backend.main 注入)
"""
import logging

from fastapi import APIRouter, BackgroundTasks, Query

from .service import (
    _json_safe,
    read_history_resilient,
    read_report_resilient,
    read_source_health_resilient,
    run_principal_capital_scan,
    write_report,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _run_scan_task(**kwargs):
    try:
        run_principal_capital_scan(**kwargs)
    except Exception as exc:
        logger.exception("主力资金后台任务异常: %s", exc)
        write_report({
            "status": "error",
            "error": str(exc),
            "buy_triggered": [],
            "sell_triggered": [],
        })


@router.post("/trigger")
async def trigger_principal_capital(
    background_tasks: BackgroundTasks,
    buy_threshold: float = Query(50.0),
    sell_threshold: float = Query(30.0),
    exclude_star: bool = Query(True),
    dry_run: bool = Query(False),
    force: bool = Query(False),
    enable_verify: bool = Query(False),
):
    try:
        write_report({
            "status": "running",
            "message": "主力资金扫描任务已启动",
            "buy_triggered": [],
            "sell_triggered": [],
        })
        background_tasks.add_task(
            _run_scan_task,
            buy_threshold=buy_threshold,
            sell_threshold=sell_threshold,
            exclude_star=exclude_star,
            dry_run=dry_run,
            force=force,
            enable_verify=enable_verify,
        )
        return {"status": "started", "message": "主力资金扫描任务已启动"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@router.get("/latest")
async def latest_principal_capital():
    try:
        return read_report_resilient()
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@router.get("/snapshot")
async def principal_capital_snapshot(history_limit: int = Query(12, ge=1, le=1000)):
    try:
        records = (read_history_resilient().get("records") or [])[-history_limit:]
        return {
            "status": "ok",
            "report": _json_safe(read_report_resilient()),
            "history": {"status": "ok", "records": _json_safe(records)},
            "source_health": _json_safe(read_source_health_resilient()),
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@router.get("/history")
async def principal_capital_history(
    direction: str = Query("all"),
    limit: int = Query(200, ge=1, le=1000),
):
    try:
        records = (read_history_resilient().get("records") or [])[-limit:]
        if direction in {"buy", "sell"}:
            records = [item for item in records if item.get("direction") == direction]
        return {"status": "ok", "records": _json_safe(records[-limit:])}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@router.get("/source-health")
async def principal_capital_source_health():
    try:
        return _json_safe(read_source_health_resilient())
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---- 09 大单分层资金流（扩展，不动核心筛选） ----
@router.get("/tier-flow/latest")
async def tier_flow_latest():
    try:
        from .tier_flow import read_latest

        return read_latest()
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@router.post("/tier-flow/trigger")
async def tier_flow_trigger(force: bool = Query(False)):
    try:
        from .tier_flow import run_tier_flow_once

        return run_tier_flow_once(force=force)
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@router.get("/tier-flow/{code}")
async def tier_flow_code(code: str, date: str = Query(None)):
    try:
        from .tier_flow import read_code_history

        return {"status": "ok", "code": code, "records": read_code_history(code, date)}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
