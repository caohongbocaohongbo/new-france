"""
筛选 API — 触发筛选、查看结果
"""
import json
import logging
import asyncio
import math
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query, BackgroundTasks, Request

from ..services.runtime_config import resolve_screening_params

router = APIRouter()
logger = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "reports"


def _enrich_latest_with_watchlist(data: dict) -> dict:
    """把监控列表里的关注字段补到最新推荐结果，保证前端可追溯来源。"""
    results = data.get("results")
    if not isinstance(results, list) or not results:
        return data

    try:
        from ..services.watchlist_store import parse_watchlist
        watchlist = {item.get("code"): item for item in parse_watchlist()}
    except Exception as exc:
        logger.warning(f"补充推荐关注字段失败: {exc}")
        return data

    for item in results:
        code = item.get("code")
        wl = watchlist.get(code)
        if not wl:
            continue
        zt_count = wl.get("zt_count", "0")
        item["added_date"] = wl.get("added_date", wl.get("zt_date", item.get("zt_date", "")))
        item["zt_count"] = zt_count
        item["follow_limit_up_count"] = zt_count
        item["seal_time"] = wl.get("seal_time", item.get("seal_time", "0"))
        item["break_count"] = wl.get("break_count", item.get("break_count", "0"))
        item["consecutive"] = wl.get("consecutive", item.get("consecutive", "0"))
        item.setdefault("limit_events", [{
            "date": wl.get("zt_date", item.get("zt_date", "")),
            "seal_time": wl.get("seal_time", "0"),
            "label": f"{wl.get('consecutive', '1') or '1'}板",
        }])
    return data


def _json_safe(value):
    """清理 NaN/Infinity，避免 FastAPI JSON 响应失败。"""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _write_json_cache(payload: dict):
    from ..services.snapshot_store import atomic_write_json

    atomic_write_json(REPORTS_DIR / "latest.json", payload)


def _cache_error(msg: str):
    """缓存错误信息到 latest.json，供前端轮询"""
    _write_json_cache({"status": "error", "message": msg})


def _execute_screening_pipeline(params: dict) -> dict:
    from ..services.screening_service import run_full_pipeline

    return asyncio.run(run_full_pipeline(
        target_date=date.today(),
        drop_min=params["drop_min"], drop_max=params["drop_max"],
        vol_min=params["vol_min"], vol_max=params["vol_max"],
        turnover_min=params["turnover_min"], turnover_max=params["turnover_max"],
        mc_min=params["mc_min"], mc_max=params["mc_max"],
        pe_max=params["pe_max"],
    ))


def _run_screening_task(params: dict):
    """后台执行筛选流水线（由 BackgroundTasks 放入线程池，不阻塞轮询接口）"""
    try:
        from ..services.task_history import append_task_record
        result = _execute_screening_pipeline(params)
        _write_json_cache({"status": "completed", **result})
        append_task_record("success", "admin")
        logger.info(f"后台筛选完成: STRONG_BUY={result['strong_buy']}, BUY={result['buy']}")

    except Exception as e:
        from ..services.task_history import append_task_record
        logger.exception(f"后台筛选异常: {e}")
        append_task_record("failed", "admin", error=str(e))
        _cache_error(str(e))


@router.post("/run")
async def run_screening(
    background_tasks: BackgroundTasks,
    drop_min: Optional[float] = Query(None, ge=0, le=20),
    drop_max: Optional[float] = Query(None, ge=0, le=20),
    vol_min: Optional[float] = Query(None, ge=0),
    vol_max: Optional[float] = Query(None, ge=0),
    turnover_min: Optional[float] = Query(None, ge=0, le=80),
    turnover_max: Optional[float] = Query(None, ge=0, le=80),
    mc_min: Optional[float] = Query(None, ge=0),
    mc_max: Optional[float] = Query(None, ge=0),
    pe_max: Optional[float] = Query(None, ge=0),
):
    """手动触发筛选 — 后台异步执行，前端轮询 /latest 获取结果"""
    params = resolve_screening_params({
        "drop_min": drop_min, "drop_max": drop_max,
        "vol_min": vol_min, "vol_max": vol_max,
        "turnover_min": turnover_min, "turnover_max": turnover_max,
        "mc_min": mc_min, "mc_max": mc_max,
        "pe_max": pe_max,
    })

    # 写入运行中状态
    _write_json_cache({"status": "running", "params": params})

    # 后台执行（不阻塞响应，避免 Render 30s 网关超时）
    background_tasks.add_task(_run_screening_task, params)

    return {
        "status": "started",
        "message": "筛选任务已启动，请轮询 /api/v1/screening/latest 查看结果",
    }


WATCHLIST_FILE = REPORTS_DIR.parent / "data" / "france.md"


@router.get("/latest")
def get_latest_screening(request: Request = None, view: str = Query("full")):
    """获取最新筛选结果（P1：早期 304；P2：view=summary 契约裁剪，默认 full 保留旧语义）。

    request=None 时退化为纯 dict 返回（兼容既有内部/单测直接调用）。
    """
    from ..middleware.performance import set_snapshot_context
    from ..services.snapshot_store import (
        dumps_bytes, etag_for_params, json_response_from_bytes, read_snapshot_entry,
    )

    cache_file = REPORTS_DIR / "latest.json"
    entry = read_snapshot_entry(cache_file)
    if entry is None:
        html_file = REPORTS_DIR / f"{date.today().strftime('%Y-%m-%d')}.html"
        if html_file.exists():
            return {"date": date.today().strftime("%Y-%m-%d"), "has_report": True,
                    "html_path": str(html_file)}
        return {"date": date.today().strftime("%Y-%m-%d"), "has_report": False,
                "results": [], "message": "今日暂无筛选报告，请先执行筛选"}
    if request is None:  # 直接调用兼容路径（无 ETag）
        try:
            return _json_safe(_enrich_latest_with_watchlist(json.loads(entry.payload_bytes.decode("utf-8"))))
        except Exception:  # noqa: BLE001
            return {"date": date.today().strftime("%Y-%m-%d"), "has_report": False,
                    "results": [], "message": "今日暂无筛选报告，请先执行筛选"}
    if view not in ("full", "summary"):
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="view 必须为 full/summary")
    wl_entry = read_snapshot_entry(WATCHLIST_FILE)
    canonical = f"watchlist={wl_entry.etag if wl_entry else 'none'}&view={view}"
    etag = etag_for_params(entry.etag, canonical)
    early = json_response_from_bytes(request, b"", etag, cache_control="no-cache", vary="Accept-Encoding")
    if early.status_code == 304:
        set_snapshot_context(request, source=entry.source or "local", cache="memory")
        return early
    try:
        data = json.loads(entry.payload_bytes.decode("utf-8"))
        enriched = _json_safe(_enrich_latest_with_watchlist(data))
    except Exception:  # noqa: BLE001 损坏快照按旧行为回退
        html_file = REPORTS_DIR / f"{date.today().strftime('%Y-%m-%d')}.html"
        if html_file.exists():
            return {"date": date.today().strftime("%Y-%m-%d"), "has_report": True,
                    "html_path": str(html_file)}
        return {"date": date.today().strftime("%Y-%m-%d"), "has_report": False,
                "results": [], "message": "今日暂无筛选报告，请先执行筛选"}
    if view == "summary":  # 契约裁剪：仅去掉前端确认未使用的字段（audit/price_history/factors 均保留）
        enriched = {k: v for k, v in enriched.items() if k not in ("report_md", "report_html")}
        results = enriched.get("results") or []
        enriched["results"] = [{k: v for k, v in it.items() if k != "evidence"} for it in results]
    set_snapshot_context(request, source=entry.source or "local", cache="memory")
    return json_response_from_bytes(request, dumps_bytes(enriched), etag,
                                    cache_control="no-cache", vary="Accept-Encoding")


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
