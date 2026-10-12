"""28 快照版本化发布（复用 common.write_snapshot 双写）。"""
from __future__ import annotations

import uuid
from datetime import datetime

from backend.plugins.common import write_snapshot
from .config import BEIJING_TZ, SNAPSHOT_NAME


def new_snapshot_version():
    return f"{datetime.now(BEIJING_TZ).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"


def publish_snapshot(payload):
    """双写 reports/ 与 reports/data_backend/，返回快照路径。"""
    return write_snapshot(SNAPSHOT_NAME, payload)


def build_snapshot_payload(run, items, *, phase, overall_confirmation, publication_kind,
                           ranking_version, ranking_asof, pool, counts, scope,
                           strategy_version, factor_spec_version, config_hash, code_commit):
    return {
        "status": "completed",
        "phase": phase,
        "trade_date": run["trade_date"],
        "strategy_version": strategy_version,
        "factor_spec_version": factor_spec_version,
        "config_hash": config_hash,
        "code_commit": code_commit,
        "branch": run.get("branch", "UNVERIFIED"),
        "snapshot_version": run.get("snapshot_version"),
        "pool_asof_date": pool.get("asof_trading_day"),
        "pool_count": counts.get("pool_count", 0),
        "scored_count": counts.get("scored_count", 0),
        "confirmed_count": counts.get("confirmed_count", 0),
        "unconfirmed_count": counts.get("unconfirmed_count", 0),
        "generated_at": datetime.now(BEIJING_TZ).isoformat(),
        "published_at": datetime.now(BEIJING_TZ).isoformat(),
        "data_quality": "VALID",
        "finalization_scope": scope,
        "ranking_version": ranking_version,
        "ranking_asof": ranking_asof,
        "overall_confirmation": overall_confirmation,
        "publication_kind": publication_kind,
        "items": items,
        "disclaimer": "仅供研究与风险观察，不构成投资建议",
    }
