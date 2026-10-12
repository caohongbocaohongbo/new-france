"""26 条件盯盘 API。"""
import logging

from fastapi import APIRouter, Body, Query

from .service import check_rules, create_rule, delete_rule, list_alerts, list_rules, read_latest, toggle_rule

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/latest")
async def watch_latest():
    try:
        return read_latest()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/rules")
async def watch_rules():
    try:
        return {"status": "ok", "items": list_rules()}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/rules")
async def watch_create(payload: dict = Body(...)):
    try:
        return create_rule(
            code=payload.get("code", ""),
            condition=payload.get("condition", ""),
            name=payload.get("name"),
            variables=payload.get("variables"),
            channels=payload.get("channels"),
            product_type=payload.get("product_type", "stock"),
        )
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.delete("/rules/{rule_id}")
async def watch_delete(rule_id: int):
    try:
        return delete_rule(rule_id)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/rules/{rule_id}/toggle")
async def watch_toggle(rule_id: int, enabled: bool = Query(True)):
    try:
        return toggle_rule(rule_id, enabled)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/alerts")
async def watch_alerts(days: int = Query(7, ge=1, le=90)):
    try:
        return {"status": "ok", "items": list_alerts(days)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/check")
async def watch_check():
    try:
        return check_rules()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
