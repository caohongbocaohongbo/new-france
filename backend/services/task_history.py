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
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    TASK_HISTORY_FILE.write_text(
        json.dumps({"records": records[-max_records:]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _fetch_snapshot_json(filename: str) -> Optional[dict]:
    """从 data-snapshots 分支拉取 reports/<filename>。失败返回 None。"""
    import requests

    url = f"{SNAPSHOT_RAW_BASE}/reports/{filename}"
    try:
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.info("定时任务远程快照拉取失败(%s): %s", filename, exc)
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
