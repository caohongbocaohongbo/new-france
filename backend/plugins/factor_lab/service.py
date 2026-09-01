"""06 因子实验室编排服务。"""
import json
import logging
from datetime import date, datetime

import pandas as pd

from backend.plugins.common import BEIJING_TZ, db_append, db_delete, db_query, json_safe, read_snapshot, write_snapshot

from .indicators import build_factor_stats

logger = logging.getLogger(__name__)
SNAPSHOT_NAME = "factor_stats"
FACTOR_NAMES = [
    "pullback", "volume_ratio", "turnover", "market_cap", "pe",
    "volume_trend", "ma_alignment", "strength", "entry_point", "zt_quality", "event_bonus",
]


def read_latest() -> dict:
    return read_snapshot(SNAPSHOT_NAME) or {"status": "empty", "items": []}


def read_factor_detail(factor: str, days: int = 90) -> list:
    df = db_query(
        "SELECT * FROM factor_daily WHERE factor_name = :f ORDER BY date DESC LIMIT :n",
        {"f": factor, "n": int(days)},
    )
    return json_safe(df.to_dict("records")) if not df.empty else []


def build_panel_from_stock_scores(forward_ret_fetcher=None) -> pd.DataFrame:
    """从 stock_scores 提取因子值，构建因子面板。forward_ret_fetcher(code, date) -> float。"""
    df = db_query("SELECT zt_date AS task_date, code, score_pullback, score_volume_ratio, score_turnover, score_market_cap, score_pe, score_volume_trend, score_ma_alignment, score_strength, score_entry_point, score_zt_quality, score_event_bonus FROM stock_scores")
    if df.empty:
        return pd.DataFrame()
    rows = []
    col_map = {
        "score_pullback": "pullback", "score_volume_ratio": "volume_ratio", "score_turnover": "turnover",
        "score_market_cap": "market_cap", "score_pe": "pe", "score_volume_trend": "volume_trend",
        "score_ma_alignment": "ma_alignment", "score_strength": "strength", "score_entry_point": "entry_point",
        "score_zt_quality": "zt_quality", "score_event_bonus": "event_bonus",
    }
    for _, r in df.iterrows():
        code = str(r["code"]).zfill(6)
        task_date = str(r["task_date"])[:10]
        for col, name in col_map.items():
            value = r.get(col)
            if value is None:
                continue
            ret = forward_ret_fetcher(code, task_date) if forward_ret_fetcher else None
            rows.append({"date": task_date, "code": code, "factor_name": name, "factor_value": float(value), "forward_ret_t1": ret})
    return pd.DataFrame(rows)


def run_factor_lab(panel: pd.DataFrame = None, force: bool = False) -> dict:
    """盘后重算因子 IC/IR/分层。panel 可注入以便离线测试。"""
    now = datetime.now(BEIJING_TZ)
    if panel is None:
        panel = build_panel_from_stock_scores()
    stats = build_factor_stats(panel)
    if not stats:
        payload = {"status": "no_data", "reason": "因子面板样本不足", "now": now.isoformat(), "items": [], "note": "样本不足时 IC 噪声大"}
        write_snapshot(SNAPSHOT_NAME, payload)
        return payload
    payload = {"status": "completed", "now": now.isoformat(), "count": len(stats), "items": stats, "note": "因子有效性随市场风格漂移，仅作研究参考"}
    write_snapshot(SNAPSHOT_NAME, payload)
    if not panel.empty:
        db_append("factor_daily", json_safe(panel.to_dict("records")))
    return payload
