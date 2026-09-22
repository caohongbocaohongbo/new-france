"""定时任务执行历史读写。"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
REPORT_DIR = PROJECT_DIR / "reports"
TASK_HISTORY_FILE = REPORT_DIR / "task_history.json"
BEIJING_TZ = timezone(timedelta(hours=8))
SNAPSHOT_RAW_BASE = os.environ.get(
    "TASK_SNAPSHOT_RAW_BASE",
    "https://raw.githubusercontent.com/caohongbocaohongbo/new-france/data-snapshots",
)


def read_task_history() -> dict:
    """读取本地执行历史。"""
    if not TASK_HISTORY_FILE.exists():
        return {"records": []}
    try:
        return json.loads(TASK_HISTORY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"records": []}


def append_task_record(
    status: str,
    operator: str,
    run_time: Optional[datetime] = None,
    error: Optional[str] = None,
    max_records: int = 500,
) -> None:
    """追加一条定时任务执行记录。"""
    current = read_task_history()
    records = current.get("records") or []
    timestamp = run_time or datetime.now(BEIJING_TZ)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=BEIJING_TZ)
    else:
        timestamp = timestamp.astimezone(BEIJING_TZ)
    records.append(
        {
            "run_time": timestamp.isoformat(),
            "run_time_display": timestamp.strftime("%Y/%m/%d %H:%M:%S"),
            "status": str(status or "").strip() or "unknown",
            "operator": str(operator or "").strip() or "unknown",
            "error": str(error or "").strip(),
        }
    )
    from .snapshot_store import atomic_write_json

    atomic_write_json(TASK_HISTORY_FILE, {"records": records[-max_records:]})


def _fetch_snapshot_json(filename: str) -> Optional[dict]:
    """远程回退统一走 snapshot_store 合并缓存（single-flight/退避/条件GET/SWR）。失败返回 None。"""
    from backend.services.snapshot_store import RemotePolicy, fetch_remote_snapshot

    key = filename.replace(".json", "").replace("/", "_")
    entry = fetch_remote_snapshot(key, f"{SNAPSHOT_RAW_BASE}/reports/{filename}", RemotePolicy(ttl_seconds=600.0))
    if entry is None:
        return None
    try:
        return dict(entry.parsed())  # 复制，不污染共享解析对象
    except Exception as exc:  # noqa: BLE001
        logger.info("定时任务远程快照解析失败(%s): %s", filename, exc)
        return None


def read_task_history_resilient() -> dict:
    """本地有记录直接返回，否则回退到 data-snapshots 快照。"""
    local = read_task_history()
    if local.get("records"):
        return local
    remote = _fetch_snapshot_json("task_history.json")
    if remote and remote.get("records"):
        return remote
    return local
