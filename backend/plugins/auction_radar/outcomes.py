"""28 预览期最小结果标签与同池基准回填（盘后，只追加）。"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

from backend.agents.layer1_data_collector.sources.historical_kline import fetch_historical
from backend.plugins.common import db_query, json_safe
from .config import BEIJING_TZ, OUTCOME_DIR

logger = logging.getLogger(__name__)


def _kline_snapshot(hist, target_date):
    """取 T 日 OHLC；核对 K 线业务日期，日期不符返回 None。"""
    if hist is None or getattr(hist, "empty", True):
        return None
    dates = None
    for col in ("日期", "date"):
        if col in hist.columns:
            dates = [str(v)[:10] for v in hist[col].tolist()]
            break
    if not dates or dates[-1] != target_date.isoformat():
        return None
    def col(*names):
        for n in names:
            if n in hist.columns:
                vals = hist[n].tolist()
                return json_safe(vals[-1])
        return None
    return {
        "open": col("开盘", "open"),
        "close": col("收盘", "close"),
        "high": col("最高", "high"),
        "low": col("最低", "low"),
    }


def append_outcome_labels(snapshot_version, label_spec_version, target_date=None):
    """盘后回填 T 日固定窗口价；追加写 + 去重；不反写当晨分数。"""
    target = target_date or datetime.now(BEIJING_TZ).date()
    if isinstance(target, datetime):
        target = target.date()
    df = db_query(
        "SELECT * FROM auction_candidates WHERE run_id IN "
        "(SELECT id FROM auction_runs WHERE trade_date = :d)",
        {"d": target.isoformat()},
    )
    if df.empty:
        return {"status": "no_candidates", "date": target.isoformat()}
    OUTCOME_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTCOME_DIR / f"{target.isoformat()}.jsonl"

    # 读已有标签去重（只追加）
    existing = {}
    if path.exists():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                existing[(row.get("snapshot_version"), row.get("code"), row.get("label_spec_version"))] = row
        except (json.JSONDecodeError, OSError):
            existing = {}

    rows = []
    for r in df.itertuples():
        code = str(r.code).zfill(6)
        key = (snapshot_version, code, label_spec_version)
        if key in existing:
            continue
        hist = None
        try:
            hist = fetch_historical(code, 3)
        except Exception as exc:  # noqa: BLE001
            logger.debug("K线拉取失败 %s: %s", code, exc)
        snap = _kline_snapshot(hist, target)
        rows.append({
            "snapshot_version": snapshot_version,
            "label_spec_version": label_spec_version,
            "trade_date": target.isoformat(),
            "code": code,
            "rank": int(r.rank) if r.rank is not None else None,
            "total_score": json_safe(r.total_score),
            "open": snap.get("open") if snap else None,
            "close": snap.get("close") if snap else None,
            "high": snap.get("high") if snap else None,
            "low": snap.get("low") if snap else None,
            "data_missing": 0 if snap and snap.get("close") is not None else 1,
            "missing_reason": None if snap else "kline_date_mismatch_or_unavailable",
        })

    if rows:
        with path.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # 六里程碑 M5：盘后结果标签回填
    run_df = db_query("SELECT id FROM auction_runs WHERE trade_date=:d ORDER BY id DESC LIMIT 1",
                      {"d": target.isoformat()})
    if not run_df.empty:
        try:
            from .service import persist_milestone
            persist_milestone(int(run_df.iloc[0]["id"]), "M5", "done",
                              {"appended": len(rows), "path": str(path)})
        except Exception:  # noqa: BLE001
            logger.debug("M5 里程碑更新失败")

    return {"status": "completed", "date": target.isoformat(), "appended": len(rows), "path": str(path)}
