"""03 T+1 溢价样本库与隔夜套利概率校准（扩展 overnight_arbitrage，不改核心决策）。

复用 overnight_arbitrage._decision_item 特征与 backtest 收益口径（无前视）。
"""
import json
import logging
import math
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from backend.plugins.common import (
    BEIJING_TZ, db_append, db_delete, db_query, float_or, json_safe, read_snapshot, write_snapshot,
)

from .config import HISTORY_FILE

logger = logging.getLogger(__name__)
SNAPSHOT_NAME = "oa_calibration"
MIN_SAMPLE = 200  # 样本 < MIN_SAMPLE 显示"样本偏少"不出概率


def bucketize(score, bucket_size: int = 10) -> str:
    """把评分映射到区间桶，如 50-60。"""
    s = float_or(score)
    if s is None:
        return "unknown"
    size = max(1, int(bucket_size))
    low = int(s // size) * size
    return f"{low}-{low + size}"


def _col(hist, *names):
    for n in names:
        if hist is not None and n in hist.columns:
            return pd.to_numeric(hist[n], errors="coerce").reset_index(drop=True)
    return None


def find_index_by_date(hist, date_str: str):
    """在历史 K 线里定位触发日行号。"""
    if hist is None or hist.empty:
        return None
    date_col = "日期" if "日期" in hist.columns else ("date" if "date" in hist.columns else None)
    if date_col is None:
        return None
    for i, v in enumerate(hist[date_col].astype(str)):
        if str(v)[:10] == str(date_str)[:10]:
            return i
    return None


def forward_returns(hist, trigger_index) -> dict:
    """无前视：只用触发日之后的 T+1 价格计算收益。"""
    closes = _col(hist, "收盘", "close")
    if trigger_index is None or closes is None or trigger_index >= len(closes) - 1:
        return {"label_t1_open": None, "label_t1_high": None, "label_t1_close": None, "label_t1_low": None}
    base = float(closes.iloc[trigger_index])
    if not base or base <= 0:
        return {"label_t1_open": None, "label_t1_high": None, "label_t1_close": None, "label_t1_low": None}
    i = trigger_index + 1
    def ret(cn, en):
        series = _col(hist, cn, en)
        if series is None or i >= len(series):
            return None
        v = float(series.iloc[i])
        return round((v - base) / base, 4) if v and v > 0 else None
    return {
        "label_t1_open": ret("开盘", "open"),
        "label_t1_high": ret("最高", "high"),
        "label_t1_close": ret("收盘", "close"),
        "label_t1_low": ret("最低", "low"),
    }


def build_samples_from_history(history: dict) -> list:
    """从隔夜套利历史提取样本（code/date/score/price/action）。"""
    samples = []
    for record in history.get("records") or []:
        code = str(record.get("code") or "").zfill(6)
        name = str(record.get("name") or "")
        for item in record.get("recommendations") or []:
            score = float_or(item.get("decision_score"))
            if score is None:
                continue
            samples.append({
                "code": code, "name": name,
                "date": str(item.get("date") or ""),
                "decision_score": score,
                "action": item.get("action"),
                "price": float_or(item.get("price")),
                "score_bucket": bucketize(score),
            })
    return samples


def calibrate(samples: list) -> dict:
    """按评分桶统计胜率/平均收益/概率；样本 < MIN_SAMPLE 提示样本偏少。"""
    if not samples:
        return {"sample_count": 0, "insufficient": True, "note": "样本偏少", "buckets": [], "overall": {}}
    buckets = {}
    for s in samples:
        buckets.setdefault(s.get("score_bucket", "unknown"), []).append(s)
    def stats(rows):
        rets = [float(r["label_t1_close"]) for r in rows if r.get("label_t1_close") is not None]
        n = len(rows)
        return {
            "count": n,
            "win_rate": round(sum(1 for v in rets if v > 0) / len(rets), 4) if rets else None,
            "avg_return": round(sum(rets) / len(rets), 4) if rets else None,
            "positive_probability": round(sum(1 for v in rets if v > 0) / len(rets), 4) if rets else None,
        }
    bucket_list = [
        {"bucket": b, **stats(rows)}
        for b, rows in sorted(buckets.items())
    ]
    total = len(samples)
    overall = stats(samples)
    return {
        "sample_count": total,
        "insufficient": total < MIN_SAMPLE,
        "note": "样本偏少" if total < MIN_SAMPLE else "",
        "buckets": bucket_list,
        "overall": overall,
    }


def enrich_labels(samples: list, hist_fetcher) -> list:
    """为样本补 T+1 收益标签（无前视，hist_fetcher(code, days) -> DataFrame）。"""
    for s in samples:
        try:
            hist = hist_fetcher(s["code"], 60)
            idx = find_index_by_date(hist, s.get("date"))
            s.update(forward_returns(hist, idx))
        except Exception:  # noqa: BLE001
            s.update({"label_t1_open": None, "label_t1_high": None, "label_t1_close": None, "label_t1_low": None})
    return samples


def read_latest() -> dict:
    return read_snapshot(SNAPSHOT_NAME) or {"status": "empty", "sample_count": 0, "insufficient": True}


def read_samples(score_bucket: str = None) -> list:
    sql = "SELECT * FROM oa_sample"
    params = {}
    if score_bucket:
        sql += " WHERE score_bucket = :b"
        params["b"] = score_bucket
    sql += " ORDER BY date DESC LIMIT 500"
    df = db_query(sql, params)
    return json_safe(df.to_dict("records")) if not df.empty else []


def run_calibration(hist_fetcher=None) -> dict:
    """盘后执行一轮校准。hist_fetcher 可注入以便离线测试。"""
    now = datetime.now(BEIJING_TZ)
    try:
        from .service import read_overnight_history
        history = read_overnight_history(HISTORY_FILE)
    except Exception:  # noqa: BLE001
        history = {"records": []}
    samples = build_samples_from_history(history)
    if hist_fetcher is not None:
        samples = enrich_labels(samples, hist_fetcher)
    result = calibrate(samples)
    payload = {
        "status": "completed", "now": now.isoformat(),
        "model_version": "naive-bayes-v1", **result,
    }
    write_snapshot(SNAPSHOT_NAME, payload)
    db_delete("oa_calibration", {"date": now.date().isoformat()})
    db_append("oa_calibration", [{"date": now.date().isoformat(), "model_version": "naive-bayes-v1", "metrics_json": json.dumps(json_safe(result), ensure_ascii=False)}])
    db_append("oa_sample", [
        {"date": s.get("date"), "code": s.get("code"),
         "features_json": json.dumps(json_safe({k: v for k, v in s.items() if k not in ("date", "code", "label_t1_open", "label_t1_high", "label_t1_close", "label_t1_low")}), ensure_ascii=False),
         "label_t1_open": s.get("label_t1_open"), "label_t1_high": s.get("label_t1_high"),
         "label_t1_close": s.get("label_t1_close"), "label_t1_low": s.get("label_t1_low"),
         "score_bucket": s.get("score_bucket")}
        for s in samples
    ])
    return payload


def run_calibration_cli(args):
    return run_calibration()
