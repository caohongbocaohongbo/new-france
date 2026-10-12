"""28 低位竞价雷达 service 基础测试（partial 阈值 + claim 幂等）。"""
import pandas as pd
import pytest

from backend.plugins.auction_radar import service


def test_partial_threshold():
    assert service._partial_threshold(5) == 3
    assert service._partial_threshold(10) == 5
    assert service._partial_threshold(3) == 3
    assert service._partial_threshold(1) == 3


def test_claim_run_once_idempotent(monkeypatch):
    def fake_append(table, rows):
        return len(rows)

    def fake_query(sql, params=None):
        p = params or {}
        if "SELECT id, state FROM auction_runs" in sql:
            if p.get("d") == "existing":
                return pd.DataFrame([{"id": 1, "state": "PUBLISHED"}])
            return pd.DataFrame()
        if "SELECT id FROM auction_runs" in sql:
            return pd.DataFrame([{"id": 2}])
        return pd.DataFrame()

    monkeypatch.setattr(service, "db_append", fake_append)
    monkeypatch.setattr(service, "db_query", fake_query)
    r1 = service.claim_run_once("new", "v1", 1)
    assert r1["claimed"] is True and r1["run_id"] == 2
    r2 = service.claim_run_once("existing", "v1", 1)
    assert r2["claimed"] is False and r2["run_id"] == 1


def test_verify_final_quote_source_time_gate():
    """源时间早于 09:25 不得因 received_at 晚而误判为最终确认。"""
    from datetime import datetime
    from backend.plugins.auction_radar.config import BEIJING_TZ
    from backend.plugins.auction_radar.models import AuctionQuote
    q = AuctionQuote(trade_date="2026-10-09", code="600000",
                     indicative_price_yuan=10.0, prev_close_yuan=9.8,
                     source_timestamp="2026-10-09T09:24:50+08:00",
                     received_at="2026-10-09T09:25:05+08:00",
                     field_capability_version="verified", quality="VALID")
    now = datetime(2026, 10, 9, 9, 26, 0, tzinfo=BEIJING_TZ)
    ev = service.verify_final_quote(q, now, ["open_price"])
    assert ev["confirmed"] is False
    assert "source_time_before_09:25" in ev["missing"]
    assert "open_price" in ev["missing"]
