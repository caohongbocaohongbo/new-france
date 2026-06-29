"""主力资金双向监控 API（plugin 独立路由）。

挂载点: /api/v1/principal-capital (由 backend.main 注入)
"""
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Query

from .config import SOURCE_HEALTH_FILE
from .service import (
    _json_safe,
    read_history,
    read_report,
    run_principal_capital_scan,
    write_report,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _read_source_health_payload() -> dict:
    if not SOURCE_HEALTH_FILE.exists():
        return {"sources": {}}
    try:
        return json.loads(SOURCE_HEALTH_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"sources": {}}


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
        return read_report()
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@router.get("/snapshot")
async def principal_capital_snapshot(history_limit: int = Query(12, ge=1, le=1000)):
    try:
        records = (read_history().get("records") or [])[-history_limit:]
        return {
            "status": "ok",
            "report": _json_safe(read_report()),
            "history": {"status": "ok", "records": _json_safe(records)},
            "source_health": _json_safe(_read_source_health_payload()),
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@router.get("/history")
async def principal_capital_history(
    direction: str = Query("all"),
    limit: int = Query(200, ge=1, le=1000),
):
    try:
        records = (read_history().get("records") or [])[-limit:]
        if direction in {"buy", "sell"}:
            records = [item for item in records if item.get("direction") == direction]
        return {"status": "ok", "records": _json_safe(records[-limit:])}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@router.get("/source-health")
async def principal_capital_source_health():
    try:
        return _json_safe(_read_source_health_payload())
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
