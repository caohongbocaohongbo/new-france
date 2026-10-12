"""28 全市场竞价快照旁路采样（只存不评分、不进榜单、不发通知）。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from backend.plugins.common import market_filter
from backend.plugins.principal_capital.sources.multi_source import fetch_market_fund_flow_resilient
from .collector import TencentAuctionSource
from .config import BEIJING_TZ, CONFIG

logger = logging.getLogger(__name__)


def _main_board_codes():
    """复用 fetch_market_fund_flow_resilient 拿全市场代码清单，再 market_filter 仅沪深主板。"""
    try:
        df, _status = fetch_market_fund_flow_resilient()
    except Exception as exc:  # noqa: BLE001
        logger.warning("全市场资金流拉取失败: %s", exc)
        return []
    if df is None or df.empty:
        return []
    df = market_filter(df, show_gem=CONFIG.get("show_gem", False),
                       show_star=CONFIG.get("show_star", False))
    return [str(c).zfill(6) for c in df["code"].tolist()]


def sample_market_once(now=None):
    """采一份全市场竞价快照存 JSONL；失败只记录状态。"""
    now = now or datetime.now(BEIJING_TZ)
    out_dir = Path(CONFIG.get("market_sample_dir"))
    out_dir.mkdir(parents=True, exist_ok=True)
    codes = _main_board_codes()
    if not codes:
        return {"status": "no_codes", "count": 0}
    src = TencentAuctionSource()
    rows = []
    try:
        src.connect()
        batch = max(1, int(CONFIG.get("batch_size", 60)))
        for i in range(0, len(codes), batch):
            part = codes[i:i + batch]
            quotes = src.fetch_quotes_batch(part)
            for code, q in quotes.items():
                rows.append({
                    "trade_date": now.date().isoformat(),
                    "sampled_at": now.isoformat(),
                    "code": code,
                    "name": q.name,
                    "indicative_price_yuan": q.indicative_price_yuan,
                    "prev_close_yuan": q.prev_close_yuan,
                    "orderbook_bid_vol_total": q.orderbook_bid_vol_total,
                    "orderbook_ask_vol_total": q.orderbook_ask_vol_total,
                    "source_timestamp": q.source_timestamp,
                    "received_at": q.received_at,
                    "raw_payload_hash": q.raw_payload_hash,
                })
    except Exception as exc:  # noqa: BLE001
        logger.warning("全市场竞价快照采样失败: %s", exc)
        return {"status": "error", "count": 0, "error": str(exc)}
    finally:
        src.disconnect()

    path = out_dir / f"{now.date().isoformat()}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"status": "completed", "count": len(rows), "path": str(path)}
