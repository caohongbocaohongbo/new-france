"""尾盘隔夜套利插件 API。"""
import hmac
import logging
import os
from datetime import datetime, timedelta, timezone

import time

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query, Request, status

from .config import HISTORY_COMPACT_FILE, HISTORY_FILE, REPORT_FILE, SNAPSHOT_RAW_BASE
from .service import (
    _OA_OK_STATUSES,
    _refine_quotes_with_tencent,
    compact_history_payload,
    compact_overnight_report,
    read_overnight_history,
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
    return asyncio.run(run_overnight_arbitrage(
        target_date=target_date,
        dry_run=dry_run,
        candidate_refiner=_refine_quotes_with_tencent,
    ))


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


def _oa_latest_entry():
    """本地完成态优先（running 不被远程旧 completed 掩盖）；否则远程合并缓存兜底。"""
    from backend.services.snapshot_store import RemotePolicy, fetch_remote_snapshot, read_snapshot_entry

    local = read_snapshot_entry(REPORT_FILE)
    if local is not None:
        status = local.parsed().get("status")
        if status in _OA_OK_STATUSES or status == "running":
            return local
    t0 = time.perf_counter()
    remote = fetch_remote_snapshot(
        "overnight_arbitrage_latest", f"{SNAPSHOT_RAW_BASE}/reports/overnight_arbitrage_latest.json",
        RemotePolicy())
    if remote is not None and remote.parsed().get("status") in _OA_OK_STATUSES:
        remote._upstream_ms = (time.perf_counter() - t0) * 1000
        return remote
    return local


@router.get("/latest")
def get_latest_overnight(request: Request, view: str = Query("compact")):
    """最新尾盘决策（P2：view=compact 默认剔除 data_quality.removed 等大字段；早期 304）。"""
    from backend.middleware.performance import set_snapshot_context
    from backend.services.snapshot_store import (
        dumps_bytes, etag_for_params, json_response_from_bytes, json_response_from_entry,
    )

    if view not in ("compact", "full"):
        raise HTTPException(status_code=422, detail="view 必须为 compact/full")
    entry = _oa_latest_entry()
    if entry is None:
        return read_overnight_report()  # 空态默认响应
    set_snapshot_context(request, source=entry.source or "local",
                         upstream_ms=getattr(entry, "_upstream_ms", None))
    if view == "full":
        return json_response_from_entry(request, entry, cache_control="no-cache", vary="Accept-Encoding")
    etag = etag_for_params(entry.etag, "view=compact")
    early = json_response_from_bytes(request, b"", etag, cache_control="no-cache", vary="Accept-Encoding")
    if early.status_code == 304:
        return early
    payload = entry.parsed()
    return json_response_from_bytes(request, dumps_bytes(compact_overnight_report(payload)), etag,
                                    cache_control="no-cache", vary="Accept-Encoding")


@router.get("/history")
def get_overnight_history(
    request: Request,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10000),
    code: str = Query(None),
    date_from: str = Query(None),
    date_to: str = Query(None),
):
    """跨日推荐统计（P2：默认不携带嵌套 recommendations；服务端分页/筛选；早期 304）。"""
    from backend.middleware.performance import set_snapshot_context
    from backend.services.snapshot_store import (
        RemotePolicy, SnapshotEntry, dumps_bytes, etag_for_params, fetch_remote_snapshot,
        json_response_from_bytes, read_snapshot_entry, weak_etag,
    )

    entry = read_snapshot_entry(HISTORY_COMPACT_FILE)
    full = read_snapshot_entry(HISTORY_FILE)
    if entry is not None and full is not None:
        # mtime 或 total_stocks 不一致 → compact artifact 过期，即时重建（防测试/异常写入污染）
        if full.stat[0] > entry.stat[0] or entry.parsed().get("total_stocks") != full.parsed().get("total_stocks"):
            entry = None
    if entry is None:
        full = full or read_snapshot_entry(HISTORY_FILE)
        if full is None:
            t0 = time.perf_counter()
            full = fetch_remote_snapshot(
                "overnight_arbitrage_history", f"{SNAPSHOT_RAW_BASE}/reports/overnight_arbitrage_history.json",
                RemotePolicy(ttl_seconds=600.0))
            upstream_ms = (time.perf_counter() - t0) * 1000 if full is not None else None
            set_snapshot_context(request, source=(full.source if full else "none"), upstream_ms=upstream_ms)
            if full is None:
                return read_overnight_history()
        else:
            set_snapshot_context(request, source=full.source or "local")
        compact_bytes = dumps_bytes(compact_history_payload(full.parsed()))
        entry = SnapshotEntry(path=full.path, payload_bytes=compact_bytes,
                              etag=weak_etag(compact_bytes), stat=full.stat,
                              loaded_at=full.loaded_at, source=full.source,
                              stale=full.stale, fetched_at=full.fetched_at,
                              refresh_error=full.refresh_error)
    else:
        set_snapshot_context(request, source=entry.source or "local")
    canonical = f"limit={limit}&offset={offset}&code={code or ''}&date_from={date_from or ''}&date_to={date_to or ''}"
    etag = etag_for_params(entry.etag, canonical)
    early = json_response_from_bytes(request, b"", etag, cache_control="no-cache", vary="Accept-Encoding")
    if early.status_code == 304:
        return early
    payload = entry.parsed()
    records = payload.get("records") or []
    if code:
        records = [r for r in records if str(r.get("code") or "").zfill(6) == str(code).zfill(6)]
    if date_from:
        records = [r for r in records if str(r.get("last_recommended_at") or "")[:10] >= date_from]
    if date_to:
        records = [r for r in records if str(r.get("last_recommended_at") or "")[:10] <= date_to]
    page = records[offset:offset + limit]
    out = {
        "status": payload.get("status"),
        "strategy": payload.get("strategy"),
        "updated_at": payload.get("updated_at"),
        "total_stocks": payload.get("total_stocks"),
        "total": len(records),
        "returned": len(page),
        "limit": limit,
        "offset": offset,
        "records": page,
    }
    return json_response_from_bytes(request, dumps_bytes(out), etag,
                                    cache_control="no-cache", vary="Accept-Encoding")


@router.get("/{code}/history")
def get_overnight_history_code(request: Request, code: str):
    """单股推荐明细（P2：嵌套 recommendations 完整返回，按需读取）。"""
    from backend.services.snapshot_store import read_snapshot_entry

    entry = read_snapshot_entry(HISTORY_FILE)
    if entry is None:
        return {"status": "empty", "code": str(code).zfill(6), "record": None}
    for record in entry.parsed().get("records") or []:
        if str(record.get("code") or "").zfill(6) == str(code).zfill(6):
            return {"status": "ok", "code": str(code).zfill(6), "record": record}
    return {"status": "ok", "code": str(code).zfill(6), "record": None, "note": "no_record"}


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
