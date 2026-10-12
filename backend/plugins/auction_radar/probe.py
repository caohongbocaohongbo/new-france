"""28 竞价源字段采样与验证（只读，输出 csv/jsonl）。"""
from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from pathlib import Path

from .collector import TencentAuctionSource
from .config import BEIJING_TZ, CONFIG

logger = logging.getLogger(__name__)


def sample_tencent(codes, out_dir, now=None):
    """采样腾讯主源竞价快照，返回 (rows, path)。"""
    now = now or datetime.now(BEIJING_TZ)
    src = TencentAuctionSource()
    try:
        src.connect()
        quotes = src.fetch_quotes_batch(codes)
    finally:
        src.disconnect()

    rows = []
    for code, q in (quotes or {}).items():
        rows.append({
            "trade_date": now.date().isoformat(),
            "code": code,
            "name": q.name,
            "source": q.source,
            "source_timestamp": q.source_timestamp,
            "received_at": q.received_at,
            "indicative_price_yuan": q.indicative_price_yuan,
            "prev_close_yuan": q.prev_close_yuan,
            "price_limit_up_yuan": q.price_limit_up_yuan,
            "price_limit_down_yuan": q.price_limit_down_yuan,
            "orderbook_bid_vol_total": q.orderbook_bid_vol_total,
            "orderbook_ask_vol_total": q.orderbook_ask_vol_total,
            "source_volume_raw": q.source_volume_raw,
            "source_amount_raw": q.source_amount_raw,
            "source_volume_unit": q.source_volume_unit,
            "field_capability_version": q.field_capability_version,
            "raw_payload_hash": q.raw_payload_hash,
        })

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / f"tencent_{now.strftime('%Y%m%d_%H%M%S')}.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    csv_path = out_dir / f"tencent_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    if rows:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    return rows, jsonl_path


def run_probe_cli(args):
    """--run-auction-radar-probe 入口；采默认股票列表。"""
    codes = ["600000", "601318", "000001", "000002", "002415"]
    rows, path = sample_tencent(codes, CONFIG.get("market_sample_dir"))
    return {"status": "completed", "count": len(rows), "path": str(path)}
