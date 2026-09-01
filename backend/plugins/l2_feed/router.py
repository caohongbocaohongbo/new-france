"""12 真实 L2 API（M1 调研快照 + 本地明细占位）。"""
import logging

from fastapi import APIRouter, Query

from .service import read_latest

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/latest")
async def l2_latest():
    try:
        return read_latest()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/orderbook/{code}")
async def l2_orderbook(code: str):
    try:
        from backend.plugins.common import db_query
        df = db_query("SELECT * FROM l2_orderbook_snapshot WHERE code = :c ORDER BY ts DESC LIMIT 1", {"c": str(code).zfill(6)})
        return {"status": "ok", "code": code, "record": df.iloc[0].to_dict() if not df.empty else None, "note": "M1 调研阶段，真实 L2 采集端后置"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/ticks/{code}")
async def l2_ticks(code: str, start: str = Query(None), end: str = Query(None)):
    try:
        from backend.plugins.common import db_query, json_safe
        sql = "SELECT * FROM l2_tick WHERE code = :c"
        params = {"c": str(code).zfill(6)}
        if start:
            sql += " AND ts >= :s"; params["s"] = start
        if end:
            sql += " AND ts <= :e"; params["e"] = end
        sql += " ORDER BY ts LIMIT 1000"
        df = db_query(sql, params)
        return {"status": "ok", "code": code, "records": json_safe(df.to_dict("records")) if not df.empty else [], "note": "M1 调研阶段，真实 L2 采集端后置"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
