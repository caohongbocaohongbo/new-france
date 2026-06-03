#!/usr/bin/env python3
"""检查旁路数据源是否达到稳定阈值，并生成待审核的接入配置变更。"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def _read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _read_health_from_branch(data_branch: str, health_file: str) -> Dict[str, Any]:
    ref = f"{data_branch}:{health_file}"
    try:
        result = subprocess.run(
            ["git", "show", ref],
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        return {"version": 1, "sources": {}}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"version": 1, "sources": {}}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _source_ready(entry: Dict[str, Any]) -> bool:
    source_status = entry.get("source_status") or {}
    return bool(
        entry.get("ok")
        and entry.get("stable")
        and entry.get("consecutive_successes", 0) >= entry.get("required_successes", 1)
        and (entry.get("data") or {}).get("record_count", 0) > 0
        and source_status.get("source")
        and source_status.get("source_url")
        and source_status.get("fetched_at")
    )


def build_promotion(config: Dict[str, Any], health: Dict[str, Any]) -> tuple[Dict[str, Any], list[Dict[str, Any]]]:
    updated = json.loads(json.dumps(config, ensure_ascii=False))
    promoted = []
    sources = updated.setdefault("sources", {})
    health_sources = health.get("sources") or {}

    for key, source_cfg in sources.items():
        entry = health_sources.get(key) or {}
        surfaces = source_cfg.setdefault("surfaces", {})
        if not _source_ready(entry):
            continue
        needs_email = not surfaces.get("email", False)
        needs_dashboard = not surfaces.get("dashboard", False)
        if not (needs_email or needs_dashboard):
            continue
        surfaces["email"] = True
        surfaces["dashboard"] = True
        promoted.append({
            "key": key,
            "label": source_cfg.get("label") or key,
            "consecutive_successes": entry.get("consecutive_successes", 0),
            "required_successes": entry.get("required_successes", 0),
            "record_count": (entry.get("data") or {}).get("record_count", 0),
            "fetched_at": (entry.get("source_status") or {}).get("fetched_at"),
            "source": (entry.get("source_status") or {}).get("source"),
            "source_url": (entry.get("source_status") or {}).get("source_url"),
        })
    return updated, promoted


def _write_evidence(path: Path, promoted: list[Dict[str, Any]], data_branch: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 可选数据源接入证据",
        "",
        f"- 数据快照分支：`{data_branch}`",
        f"- 生成时间：`{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}`",
        "",
    ]
    if not promoted:
        lines.append("没有达到稳定阈值且需要接入邮件/Dashboard 的数据源。")
    for item in promoted:
        lines.extend([
            f"## {item['label']}",
            "",
            f"- 连续成功：{item['consecutive_successes']} / {item['required_successes']}",
            f"- 数据条数：{item['record_count']}",
            f"- 最近采集：{item.get('fetched_at') or '--'}",
            f"- 来源：{item.get('source') or '--'}",
            f"- 来源链接：{item.get('source_url') or '--'}",
            "",
        ])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="检查旁路数据源是否可接入生产展示")
    parser.add_argument("--config", default="config/optional_sources.json")
    parser.add_argument("--health-file", default="data/source_health.json")
    parser.add_argument("--data-branch", default="data-snapshots")
    parser.add_argument("--evidence-file", default="reports/optional-source-promotion.md")
    args = parser.parse_args()

    config_path = Path(args.config)
    config = _read_json(config_path, {"version": 1, "sources": {}})
    health = _read_health_from_branch(args.data_branch, args.health_file)
    updated, promoted = build_promotion(config, health)
    _write_evidence(Path(args.evidence_file), promoted, args.data_branch)

    if not promoted:
        print("promotion_required=false")
        return 0

    _write_json(config_path, updated)
    print("promotion_required=true")
    print("promoted_sources=" + ",".join(item["key"] for item in promoted))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
