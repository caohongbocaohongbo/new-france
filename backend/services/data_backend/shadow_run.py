"""部署地影子运行观测（§12.4 第5步，代码侧准备）。

记录每轮数据面的 source/coverage/行情年龄/错误/截止时间达成，供 5 交易日观测聚合。
本机可测试聚合逻辑；真实观测在部署地开启 SHADOW_RUN_ENABLED=1 后写入。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from backend.plugins.common import BEIJING_TZ, now_beijing

PROJECT_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_DIR / "data"
SHADOW_FILE = DATA_DIR / "shadow_run.json"
_MAX_RECORDS = 20000


def enabled() -> bool:
    return os.environ.get("SHADOW_RUN_ENABLED", "").lower() in {"1", "true", "yes"}


def _read() -> list:
    if not SHADOW_FILE.exists():
        return []
    try:
        payload = json.loads(SHADOW_FILE.read_text(encoding="utf-8"))
        return payload.get("records") or []
    except (json.JSONDecodeError, OSError):
        return []


def _write(records: list) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SHADOW_FILE.write_text(
        json.dumps({"records": records[-_MAX_RECORDS:]}, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8",
    )


def record(asset: str, source: str, status: str, *, coverage: Optional[dict] = None,
           source_time: Optional[str] = None, age_seconds: Optional[float] = None,
           error: Optional[str] = None, deadline_met: Optional[bool] = None) -> None:
    """记录一轮；未开启时静默跳过。"""
    if not enabled():
        return
    records = _read()
    records.append({
        "ts": now_beijing().isoformat(),
        "asset": asset, "source": source, "status": status,
        "coverage": coverage or {},
        "source_time": source_time,
        "age_seconds": age_seconds,
        "error": error,
        "deadline_met": deadline_met,
    })
    _write(records)


def summarize() -> dict:
    """按 asset×source 聚合：轮数、成功率、行情年龄 P95、截止达成率、错误类型。"""
    records = _read()
    groups = {}
    for r in records:
        key = (r.get("asset"), r.get("source"))
        g = groups.setdefault(key, {"runs": 0, "errors": 0, "ages": [], "deadline": [], "error_types": {}})
        g["runs"] += 1
        if r.get("status") in ("error", "unavailable", "degraded") or r.get("error"):
            g["errors"] += 1
        if r.get("error"):
            g["error_types"][r["error"][:60]] = g["error_types"].get(r["error"][:60], 0) + 1
        if r.get("age_seconds") is not None:
            g["ages"].append(float(r["age_seconds"]))
        if r.get("deadline_met") is not None:
            g["deadline"].append(bool(r["deadline_met"]))
    out = {}
    for (asset, source), g in sorted(groups.items()):
        ages = sorted(g["ages"])
        p95 = ages[int(len(ages) * 0.95)] if ages else None
        dl = g["deadline"]
        out[f"{asset}@{source}"] = {
            "runs": g["runs"],
            "error_rate": round(g["errors"] / g["runs"], 4) if g["runs"] else None,
            "age_p95_seconds": p95,
            "deadline_met_rate": round(sum(dl) / len(dl), 4) if dl else None,
            "error_types": dict(sorted(g["error_types"].items(), key=lambda kv: -kv[1])[:5]),
        }
    return out


def reset() -> None:
    _write([])
