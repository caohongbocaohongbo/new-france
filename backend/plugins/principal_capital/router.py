"""主力资金双向监控 API（plugin 独立路由）。

挂载点: /api/v1/principal-capital (由 backend.main 注入)
"""
import logging
import time

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request

from .service import (
    _json_safe,
    read_history_resilient,
    read_report_resilient,
    read_source_health_resilient,
    run_principal_capital_scan,
    write_report,
)
from .config import REPORT_DIR

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
def latest_principal_capital():  # 同步 def：阻塞读（含远程兜底）走线程池（P0-3）
    try:
        return read_report_resilient()
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@router.get("/snapshot")
def principal_capital_snapshot(history_limit: int = Query(12, ge=1, le=1000)):
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
def principal_capital_history(
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
def principal_capital_source_health():
    try:
        return _json_safe(read_source_health_resilient())
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---- 09 大单分层资金流（扩展，不动核心筛选） ----
# P2 契约：view=compact 默认 ≤100 条（硬上限 200），服务端分页；字段白名单扩展
_TIER_COMPACT_FIELDS = ("code", "name", "super_net", "big_net", "smart_ratio", "state")
_TIER_FIELD_WHITELIST = _TIER_COMPACT_FIELDS + (
    "price", "change_pct", "total_amount", "mid_net", "small_net",
    "super_ratio", "big_ratio", "vwap_large",
)


@router.get("/tier-flow/latest")
def tier_flow_latest(
    request: Request,
    view: str = Query("full"),  # 迁移期默认 full（P1-1 兼容旧调用方）；前端显式传 view=compact
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10000),
    fields: str = Query(""),
):
    """分层资金流列表：compact=前端字段契约（默认）；full=完整快照（诊断）。"""
    from backend.middleware.performance import set_snapshot_context
    from backend.plugins.common import SNAPSHOT_RAW_BASE
    from backend.services.snapshot_store import (
        RemotePolicy, dumps_bytes, etag_for_params, fetch_remote_snapshot, json_response_from_bytes,
        json_response_from_entry, read_snapshot_entry,
    )

    if view not in ("compact", "full"):
        raise HTTPException(status_code=422, detail="view 必须为 compact/full")
    field_list = list(_TIER_COMPACT_FIELDS)
    if fields:
        for f in str(fields).split(","):
            f = f.strip()
            if not f:
                continue
            if f not in _TIER_FIELD_WHITELIST:
                raise HTTPException(status_code=422, detail=f"fields 不在白名单: {f}")
            if f not in field_list:
                field_list.append(f)
    entry = read_snapshot_entry(REPORT_DIR / "tier_flow_latest.json")
    source, cache = "none", "miss"
    if entry is None:  # Render 无本地快照 → 远程合并缓存兜底
        t0 = time.perf_counter()
        entry = fetch_remote_snapshot(
            "tier_flow", f"{SNAPSHOT_RAW_BASE}/reports/data_backend/tier_flow_latest.json",
            RemotePolicy())
        upstream_ms = (time.perf_counter() - t0) * 1000
        if entry is not None:
            source, cache = entry.source, "remote"
    else:
        source, cache, upstream_ms = entry.source, "memory", None
    if entry is None:
        return {"status": "no_data", "reason": "tier_flow_unavailable"}
    set_snapshot_context(request, source=source, cache=cache, upstream_ms=upstream_ms)
    if view == "full":
        return json_response_from_entry(request, entry, cache_control="no-cache", vary="Accept-Encoding")
    canonical = f"view=compact&limit={limit}&offset={offset}&fields={fields}"
    etag = etag_for_params(entry.etag, canonical)
    early = json_response_from_bytes(request, b"", etag, cache_control="no-cache", vary="Accept-Encoding")
    if early.status_code == 304:
        return early
    payload = entry.parsed()
    items = payload.get("items") or []
    page = []
    for it in items[offset:offset + limit]:
        page.append({f: it.get(f) for f in field_list})
    compact = {
        "status": payload.get("status"),
        "now": payload.get("now"),
        "active_source": payload.get("active_source"),
        "degraded": payload.get("degraded"),
        "states": payload.get("states"),
        "total": len(items),
        "returned": len(page),
        "limit": limit,
        "offset": offset,
        "items": page,
    }
    return json_response_from_bytes(request, dumps_bytes(compact), etag,
                                    cache_control="no-cache", vary="Accept-Encoding")


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
