"""数据后台只读聚合视图。

严格只读: 仅读取本地快照/health 文件的元信息, 绝不触发任何现拉。
状态面板必须永远能秒开, 不得因外部数据源抖动而变慢或报错。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from . import registry
from .trading_session import current_trading_session

BEIJING_TZ = timezone(timedelta(hours=8))


def get_data_assets_overview() -> Dict[str, Any]:
    assets = registry.get_all_assets()
    summary = {"fresh": 0, "stale": 0, "degraded": 0, "unavailable": 0}
    for item in assets:
        status = str(item.get("status") or "unavailable")
        summary[status] = summary.get(status, 0) + 1
    return {
        "generated_at": datetime.now(BEIJING_TZ).isoformat(),
        "trading_session": current_trading_session(),
        "assets": assets,
        "summary": summary,
    }
