"""09 大单分层资金流与吸筹/出货/拉升状态机（扩展 principal_capital，不改核心筛选）。

复用 multi_source.fetch_market_fund_flow_resilient + service._base_filter，
把 f66 超大单 / f72 大单 / f78 中单 / f84 小单 拆解为 smart_ratio + 状态机。
"""
import logging
import math
import os
from datetime import datetime

import pandas as pd

from backend.api.router_system import trading_session_status
from backend.plugins.common import (
    BEIJING_TZ, db_append, db_query, float_or, json_safe, read_snapshot, write_snapshot,
)
from .config import REPORT_DIR
from .service import _base_filter
from .sources.multi_source import fetch_market_fund_flow_resilient

logger = logging.getLogger(__name__)

SNAPSHOT_NAME = "tier_flow"


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


# 状态机阈值（TIER_* env 覆盖）
TIER_UP_CHANGE = _env_float("TIER_UP_CHANGE", 1.0)      # 涨幅 >= 1% 视为价格上行
TIER_SMART_MIN = _env_float("TIER_SMART_MIN", 0.0)      # smart_net > 0 视为大单净买


def classify_state(smart_net: float, change_pct: float, up_change: float = None) -> str:
    """二维状态机：smart_net(大单净额) × change_pct(涨跌幅)。

    - 拉升：大单净买 + 价格上行
    - 吸筹：大单净买 + 价格横盘/微跌
    - 出货：大单净卖（含拉高出货与下跌派发）
    - 观望：大单净额为零或数据缺失
    """
    up = float(up_change) if up_change is not None else TIER_UP_CHANGE
    if smart_net is None or change_pct is None:
        return "观望"
    if smart_net > TIER_SMART_MIN:
        return "拉升" if change_pct >= up else "吸筹"
    if smart_net < -TIER_SMART_MIN:
        return "出货"
    return "观望"


def build_tier_rows(df: pd.DataFrame) -> list:
    """把过滤后的全市场资金流 DataFrame 转为分层快照行（含状态机）。"""
    rows = []
    for _, row in df.iterrows():
        code = str(row.get("code", "")).zfill(6)
        total = float_or(row.get("total_amount"), 0) or 0
        super_net = float_or(row.get("super_net"), 0) or 0
        big_net = float_or(row.get("big_net"), 0) or 0
        mid_net = float_or(row.get("mid_net"), 0) or 0
        small_net = float_or(row.get("small_net"), 0) or 0
        change_pct = float_or(row.get("change_pct"), 0) or 0
        price = float_or(row.get("price"), 0) or 0
        smart_net = super_net + big_net
        smart_ratio = round(smart_net / total * 100, 4) if total > 0 else None
        super_ratio = round(super_net / total * 100, 4) if total > 0 else None
        big_ratio = round(big_net / total * 100, 4) if total > 0 else None
        rows.append({
            "code": code,
            "name": str(row.get("name", "")),
            "price": price,
            "change_pct": change_pct,
            "total_amount": total,
            "super_net": super_net,
            "big_net": big_net,
            "mid_net": mid_net,
            "small_net": small_net,
            "super_ratio": super_ratio,
            "big_ratio": big_ratio,
            "smart_ratio": smart_ratio,
            "vwap_large": round(price, 2),  # 大单成本带近似=现价（精确分价见 11）
            "state": classify_state(smart_net, change_pct),
        })
    rows.sort(key=lambda item: item["smart_ratio"] if item["smart_ratio"] is not None else -math.inf, reverse=True)
    return rows


def run_tier_flow_once(now: datetime = None, force: bool = False) -> dict:
    """执行一轮大单分层资金流聚合。"""
    now = now or datetime.now(BEIJING_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=BEIJING_TZ)
    session = trading_session_status(now)
    if not force and not session.get("is_trading_hours"):
        payload = {
            "status": "skipped", "reason": session.get("market_status_text", "非交易时段"),
            "now": now.isoformat(), "count": 0, "items": [],
        }
        write_snapshot(SNAPSHOT_NAME, payload)
        return payload
    df, source_status = fetch_market_fund_flow_resilient()
    if df is None or df.empty:
        payload = {
            "status": "no_data", "reason": "资金流数据为空",
            "now": now.isoformat(), "active_source": (source_status or {}).get("active_source"),
            "count": 0, "items": [],
        }
        write_snapshot(SNAPSHOT_NAME, payload)
        return payload
    filtered = _base_filter(df, exclude_star=False, min_amount=0)  # 全主板覆盖
    rows = build_tier_rows(filtered)
    states = {}
    for item in rows:
        states[item["state"]] = states.get(item["state"], 0) + 1
    payload = {
        "status": "completed",
        "now": now.isoformat(),
        "active_source": (source_status or {}).get("active_source"),
        "degraded": bool((source_status or {}).get("is_stale")),
        "count": len(rows),
        "states": states,
        "items": rows,
    }
    write_snapshot(SNAPSHOT_NAME, payload)
    # 最佳努力落 SQLite（date/hhmm 维度）
    db_rows = [
        {
            "date": now.date().isoformat(), "hhmm": now.strftime("%H:%M"),
            **{k: item.get(k) for k in ("code", "name", "super_net", "big_net", "mid_net", "small_net", "smart_ratio", "vwap_large", "state")},
        }
        for item in rows
    ]
    db_append("tier_flow_snapshot", db_rows)
    return payload


def read_latest() -> dict:
    return read_snapshot(SNAPSHOT_NAME) or {"status": "empty", "items": [], "count": 0}


def read_code_history(code: str, date_str: str = None) -> list:
    """按 code 读当日分层快照时间线（SQLite）。"""
    code = str(code).zfill(6)
    sql = "SELECT * FROM tier_flow_snapshot WHERE code = :code"
    params = {"code": code}
    if date_str:
        sql += " AND date = :date"
        params["date"] = date_str
    sql += " ORDER BY date, hhmm"
    df = db_query(sql, params)
    if df.empty:
        return []
    return json_safe(df.to_dict("records"))


def run_tier_flow_cli(args):
    return run_tier_flow_once(force=getattr(args, "force", False))
