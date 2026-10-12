"""28 端到端：多轮采集→09:25 后确认→部分确认保位次。"""
import asyncio
from datetime import datetime

import pytest

from backend.plugins.auction_radar import service
from backend.plugins.auction_radar.config import BEIJING_TZ
from backend.plugins.auction_radar.models import AuctionQuote, FrozenPool
from backend.plugins.auction_radar.store import AuctionStore


def _quote(code, name, price, prev, *, final_open=None, src_ts=None, capability="verified"):
    return AuctionQuote(
        trade_date="2026-10-09", code=code, name=name,
        indicative_price_yuan=price, prev_close_yuan=prev,
        final_open_price_yuan=final_open,
        orderbook_bid_vol_total=100, orderbook_ask_vol_total=50,
        price_limit_up_yuan=round(prev * 1.1, 2), price_limit_down_yuan=round(prev * 0.9, 2),
        source_timestamp=src_ts or "2026-10-09T09:25:05+08:00",
        received_at="2026-10-09T09:25:05+08:00",
        field_capability_version=capability, quality="VALID",
    )


def _mock_deps(monkeypatch):
    monkeypatch.setattr(service, "claim_run_once", lambda d, s, r: {"claimed": True, "run_id": 99, "state": "INIT"})
    monkeypatch.setattr(service, "update_run_checked", lambda rid, **f: 1)
    monkeypatch.setattr(service, "update_run", lambda rid, **f: 1)
    monkeypatch.setattr(service, "publish_snapshot", lambda payload: None)
    monkeypatch.setattr(service, "db_delete", lambda t, w: 0)
    monkeypatch.setattr(service, "db_append", lambda t, rows: len(rows))
    # 让 finalize 只跑一轮就进入部分确认分支
    service.CONFIG["final_fast_deadline_s"] = 0.01
    service.CONFIG["final_confirm_deadline_s"] = 0.01
    service.CONFIG["final_retry_interval_s"] = 0.0
    service.CONFIG["top_n"] = 5


def test_e2e_partial_confirmed_preserves_rank(monkeypatch):
    _mock_deps(monkeypatch)
    pool_items = [{"code": f"60000{i}", "name": f"票{i}", "low_score": 60.0 - i} for i in range(5)]
    frozen = FrozenPool(trade_date="2026-10-09", asof_trading_day="2026-10-08",
                        pool_hash="h", items=pool_items, total_count=5)
    store = AuctionStore()
    store.frozen_pool = frozen

    # 模拟 09:15–09:25 多轮采集累积帧
    for i in range(5):
        q = _quote(f"60000{i}", f"票{i}", 10 + i, 9.8)
        store.add_auction_frame(q.code, q, datetime(2026, 10, 9, 9, 16, 0, tzinfo=BEIJING_TZ))

    # 09:25 后最终确认：3 只确认，2 只缺 final_open（源时间早于 09:25）
    async def fake_poll(source, codes, cfg=None, deadline=None):
        rows = []
        for i, c in enumerate(codes):
            if i < 3:
                q = _quote(c, f"票{i}", 10 + i, 9.8, final_open=10 + i)
            else:
                q = _quote(c, f"票{i}", 10 + i, 9.8, src_ts="2026-10-09T09:24:50+08:00")
            rows.append({"code": c, "quote": q, "error": None})
        return rows

    monkeypatch.setattr(service, "poll_pool_once", fake_poll)

    now = datetime(2026, 10, 9, 9, 26, 0, tzinfo=BEIJING_TZ)
    result = asyncio.run(service.finalize_once("2026-10-09", "auction_v1_limited", store, None, now, dry_run=True))

    assert result["overall_confirmation"] == "PARTIAL_CONFIRMED"
    assert result["publication_kind"] == "OBSERVATION_ONLY"
    items = result["items"]
    assert len(items) == 5
    # 保位次：rank 1..5 都在，未确认票带 missing_fields
    ranks = sorted(it["original_rank"] for it in items)
    assert ranks == [1, 2, 3, 4, 5]
    confirmed = [it for it in items if it["item_confirmation"] == "CONFIRMED"]
    unconfirmed = [it for it in items if it["item_confirmation"] == "UNCONFIRMED"]
    assert len(confirmed) == 3 and len(unconfirmed) == 2
    for it in unconfirmed:
        assert "source_time_before_09:25" in it["missing_fields"]


def test_finalize_full_confirmed(monkeypatch):
    _mock_deps(monkeypatch)
    pool_items = [{"code": f"60000{i}", "name": f"票{i}", "low_score": 60.0 - i} for i in range(3)]
    frozen = FrozenPool(trade_date="2026-10-09", asof_trading_day="2026-10-08",
                        pool_hash="h", items=pool_items, total_count=3)
    store = AuctionStore()
    store.frozen_pool = frozen
    for i in range(3):
        q = _quote(f"60000{i}", f"票{i}", 10 + i, 9.8)
        store.add_auction_frame(q.code, q, datetime(2026, 10, 9, 9, 16, 0, tzinfo=BEIJING_TZ))

    async def fake_poll(source, codes, cfg=None, deadline=None):
        return [{"code": c, "quote": _quote(c, f"票{i}", 10 + i, 9.8, final_open=10 + i), "error": None}
                for i, c in enumerate(codes)]

    monkeypatch.setattr(service, "poll_pool_once", fake_poll)
    now = datetime(2026, 10, 9, 9, 25, 1, tzinfo=BEIJING_TZ)
    result = asyncio.run(service.finalize_once("2026-10-09", "auction_v1_limited", store, None, now, dry_run=True))
    assert result["overall_confirmation"] == "CONFIRMED"
    assert all(it["item_confirmation"] == "CONFIRMED" for it in result["items"])
