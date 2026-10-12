"""28 关闭标准测试：R-01 真实写入 / R-02 最终重排 / R-05 dry_run 隔离 / R-08 trigger 校验。"""
import asyncio
from datetime import datetime

import pytest

from backend.plugins.auction_radar import router, service
from backend.plugins.auction_radar.config import BEIJING_TZ, PRELIMINARY_FILE
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


def _deps(monkeypatch, *, top_n=4):
    monkeypatch.setattr(service, "claim_run_once",
                        lambda d, s, r: {"claimed": True, "run_id": 99, "state": "INIT"})
    monkeypatch.setattr(service, "update_run_checked", lambda rid, **f: 1)
    monkeypatch.setattr(service, "update_run", lambda rid, **f: 1)
    monkeypatch.setattr(service, "publish_snapshot", lambda payload: None)
    monkeypatch.setattr(service, "db_delete", lambda t, w: 0)
    monkeypatch.setattr(service, "db_append", lambda t, rows: len(rows))
    service.CONFIG["final_fast_deadline_s"] = 0.01
    service.CONFIG["final_confirm_deadline_s"] = 0.01
    service.CONFIG["final_retry_interval_s"] = 0.0
    service.CONFIG["top_n"] = top_n
    service.CONFIG["branch"] = "B-"


def test_r01_write_preliminary_real_file(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "PRELIMINARY_FILE", tmp_path / "preliminary.json")
    payload = service.write_preliminary([{"code": "600000", "rank": 1}],
                                        datetime(2026, 10, 9, 9, 20, 0, tzinfo=BEIJING_TZ))
    assert payload["phase"] == "PRELIMINARY"
    assert (tmp_path / "preliminary.json").exists()
    import json
    data = json.loads((tmp_path / "preliminary.json").read_text(encoding="utf-8"))
    assert data["phase"] == "PRELIMINARY" and len(data["items"]) == 1


def test_r02_final_reprice_moves_into_topn(monkeypatch):
    _deps(monkeypatch, top_n=4)
    pool_items = [{"code": f"60000{i}", "name": f"票{i}", "low_score": 60.0} for i in range(5)]
    frozen = FrozenPool(trade_date="2026-10-09", asof_trading_day="2026-10-08",
                        pool_hash="h", items=pool_items, total_count=5)
    store = AuctionStore()
    store.frozen_pool = frozen
    # 竞价阶段：600004 涨幅最低（预测第 5）
    for i in range(5):
        q = _quote(f"60000{i}", f"票{i}", 10.1 + i * 0.01, 10.0)
        store.add_auction_frame(q.code, q, datetime(2026, 10, 9, 9, 16, 0, tzinfo=BEIJING_TZ))

    async def fake_poll(source, codes, cfg=None, deadline=None):
        rows = []
        for i, c in enumerate(codes):
            if i == 4:
                # 最终报价跳升，gap 最大，应升至 TopN
                q = _quote(c, "票4", 10.8, 10.0, final_open=10.8)
            else:
                q = _quote(c, f"票{i}", 10.1 + i * 0.01, 10.0, final_open=10.1 + i * 0.01)
            rows.append({"code": c, "quote": q, "error": None})
        return rows

    monkeypatch.setattr(service, "poll_pool_once", fake_poll)
    now = datetime(2026, 10, 9, 9, 25, 1, tzinfo=BEIJING_TZ)
    result = asyncio.run(service.finalize_once("2026-10-09", "auction_v1_limited", store, None, now, dry_run=True))
    codes = [it["code"] for it in result["items"]]
    assert "600004" in codes  # 最终重排后进入 TopN


def test_r02_missing_code_no_full_scope_publish(monkeypatch):
    _deps(monkeypatch, top_n=4)
    pool_items = [{"code": f"60000{i}", "name": f"票{i}", "low_score": 60.0} for i in range(5)]
    frozen = FrozenPool(trade_date="2026-10-09", asof_trading_day="2026-10-08",
                        pool_hash="h", items=pool_items, total_count=5)
    store = AuctionStore()
    store.frozen_pool = frozen
    for i in range(5):
        q = _quote(f"60000{i}", f"票{i}", 10.1 + i * 0.01, 10.0)
        store.add_auction_frame(q.code, q, datetime(2026, 10, 9, 9, 16, 0, tzinfo=BEIJING_TZ))

    async def fake_poll(source, codes, cfg=None, deadline=None):
        # 缺 600004：全池范围不完整
        rows = [{"code": c, "quote": _quote(c, f"票{i}", 10.1 + i * 0.01, 10.0, final_open=10.1 + i * 0.01), "error": None}
                for i, c in enumerate(codes) if i != 4]
        return rows

    monkeypatch.setattr(service, "poll_pool_once", fake_poll)
    now = datetime(2026, 10, 9, 9, 26, 0, tzinfo=BEIJING_TZ)
    result = asyncio.run(service.finalize_once("2026-10-09", "auction_v1_limited", store, None, now, dry_run=True))
    # 范围不完整：不得 CONFIRMED 或 PARTIAL_CONFIRMED 完整范围榜单
    assert result.get("status") == "unconfirmed" or result.get("overall_confirmation") == "UNCONFIRMED"


def test_r05_dry_run_does_not_claim(monkeypatch):
    _deps(monkeypatch)
    claimed = []
    monkeypatch.setattr(service, "claim_run_once", lambda d, s, r: claimed.append(1) or {"claimed": True, "run_id": 1, "state": "INIT"})
    frozen = FrozenPool(trade_date="2026-10-09", asof_trading_day="2026-10-08", pool_hash="h",
                        items=[{"code": "600000", "name": "票0", "low_score": 60.0}], total_count=1)
    store = AuctionStore()
    store.frozen_pool = frozen
    store.add_auction_frame("600000", _quote("600000", "票0", 10.1, 10.0),
                            datetime(2026, 10, 9, 9, 16, 0, tzinfo=BEIJING_TZ))
    async def fake_poll(source, codes, cfg=None, deadline=None):
        return [{"code": "600000", "quote": _quote("600000", "票0", 10.1, 10.0, final_open=10.1), "error": None}]
    monkeypatch.setattr(service, "poll_pool_once", fake_poll)
    now = datetime(2026, 10, 9, 9, 25, 1, tzinfo=BEIJING_TZ)
    asyncio.run(service.finalize_once("2026-10-09", "auction_v1_limited", store, None, now, dry_run=True))
    assert claimed == []  # dry_run 未抢占正式 run


def test_r08_trigger_token_gate(monkeypatch):
    calls = []
    async def fake_run(dry_run, force):
        calls.append(1)
        return {"status": "ok"}
    monkeypatch.setattr(router, "run_auction_radar_once", fake_run)
    from fastapi import HTTPException

    # 关闭
    monkeypatch.setattr(router, "CONFIG", {"trigger_enabled": False, "trigger_token": "abc"})
    with pytest.raises(HTTPException) as e:
        asyncio.run(router.trigger(True, False, authorization=None, x_auction_token="abc"))
    assert e.value.status_code == 404

    # 空 token
    monkeypatch.setattr(router, "CONFIG", {"trigger_enabled": True, "trigger_token": ""})
    with pytest.raises(HTTPException) as e:
        asyncio.run(router.trigger(True, False, authorization=None, x_auction_token="abc"))
    assert e.value.status_code == 403

    # 无 token
    monkeypatch.setattr(router, "CONFIG", {"trigger_enabled": True, "trigger_token": "abc"})
    with pytest.raises(HTTPException) as e:
        asyncio.run(router.trigger(True, False, authorization=None, x_auction_token=None))
    assert e.value.status_code == 401

    # 错 token
    with pytest.raises(HTTPException) as e:
        asyncio.run(router.trigger(True, False, authorization=None, x_auction_token="wrong"))
    assert e.value.status_code == 403

    # 正确 token
    r = asyncio.run(router.trigger(True, False, authorization=None, x_auction_token="abc"))
    assert r["status"] == "ok" and len(calls) == 1


def test_r04_auction_phase_boundary():
    from backend.plugins.auction_radar.scheduler import auction_phase
    tz = BEIJING_TZ
    assert auction_phase(datetime(2026, 10, 9, 9, 14, 59, tzinfo=tz)) == "pre"
    assert auction_phase(datetime(2026, 10, 9, 9, 15, 0, tzinfo=tz)) == "collect"
    assert auction_phase(datetime(2026, 10, 9, 9, 24, 59, tzinfo=tz)) == "collect"
    assert auction_phase(datetime(2026, 10, 9, 9, 25, 0, tzinfo=tz)) == "finalize"
    assert auction_phase(datetime(2026, 10, 9, 9, 25, 59, tzinfo=tz)) == "finalize"
    assert auction_phase(datetime(2026, 10, 9, 9, 26, 0, tzinfo=tz)) == "finalize"
