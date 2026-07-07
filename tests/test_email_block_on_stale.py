import asyncio
from datetime import date
from unittest.mock import AsyncMock, patch

import pandas as pd

from backend.agents.layer2_signal_engine.scoring import ScoredStock
from backend.agents.layer2_signal_engine.skills.base import SkillResult
from backend.services import screening_service as svc


def _scored_stock():
    return ScoredStock(
        code="000656",
        name="金科股份",
        zt_date="2026-07-06",
        ref_price=1.43,
        current_price=1.35,
        drop_pct=-5.59,
        factor_scores={
            "pullback": SkillResult("回撤幅度", "pullback", 8.0, 0.14, "回撤落入区间", True),
        },
        total_score=41.1,
        event_impact=0.0,
        adjusted_score=41.1,
        rank=1,
        recommendation="WATCH",
        extra={
            "added_date": "2026-07-02",
            "price_history": [{"date": "2026-07-07", "close": 1.35, "drawdown_pct": -5.59, "change_pct": -1.0}],
        },
    )


def test_validate_core_sources_for_date_returns_problems_for_stale_assets():
    problems = svc._validate_core_sources_for_date(
        date(2026, 7, 7),
        {
            "zt_pool": {"fetched_at": "2026-07-06T17:43:40+08:00", "status": "fresh"},
            "index_snapshot": {"fetched_at": "2026-07-07T15:10:00+08:00", "status": "fresh"},
            "quotes": {"fetched_at": "2026-07-07T15:10:00+08:00", "status": "degraded"},
        },
    )

    assert problems == [
        {
            "asset": "zt_pool",
            "expected_date": "2026-07-07",
            "fetched_at": "2026-07-06T17:43:40+08:00",
            "status": "fresh",
        },
        {
            "asset": "quotes",
            "expected_date": "2026-07-07",
            "fetched_at": "2026-07-07T15:10:00+08:00",
            "status": "degraded",
        },
    ]


def test_run_full_pipeline_blocks_email_when_core_sources_are_stale():
    zt_pool = pd.DataFrame([
        {"代码": "000656", "名称": "金科股份", "最新价": 1.43, "涨跌幅": 10.0, "换手率": 6.93, "流通市值": 10849909080.0, "封板时间": 92500, "炸板次数": 4, "连板数": 3}
    ])
    quotes = pd.DataFrame([
        {"代码": "000656", "名称": "金科股份", "最新价": 1.35, "涨跌幅": -5.59, "换手率": 6.93, "量比": 4.53, "市盈率": -89.62, "总市值": 15141886234.0, "流通市值": 10849909080.0}
    ])
    historical = {
        "000656": pd.DataFrame([
            {"日期": "2026-07-06", "收盘": 1.43, "涨跌幅": 10.0},
            {"日期": "2026-07-07", "收盘": 1.35, "涨跌幅": -5.59},
        ])
    }
    stock = _scored_stock()
    recom_execute = AsyncMock(
        return_value={
            "total_scored": 1,
            "strong_buy": 0,
            "buy": 0,
            "watch": 1,
            "report_md": "/tmp/report.md",
            "report_html": "/tmp/report.html",
            "notified": False,
        }
    )

    with patch.object(svc, "get_effective_config", return_value={"config": {"strategy": {"trackingDays": 30}}}), \
         patch.object(svc, "get_factor_weights_decimal", return_value={}), \
         patch.object(svc, "_read_index_snapshot_with_cache", return_value=(
             {"value": 4041.24, "gain_pct": -0.06, "source": "东方财富", "fetched_at": "2026-07-06T17:44:56+08:00"},
             {"fetched_at": "2026-07-06T17:44:56+08:00", "status": "fresh", "source": "eastmoney"},
         )), \
         patch.object(svc, "_read_zt_pool_with_cache", return_value=(
             zt_pool,
             {"fetched_at": "2026-07-06T17:43:40+08:00", "status": "fresh", "source": "eastmoney_direct"},
         )), \
         patch.object(svc, "_read_quotes_with_cache", return_value=(
             quotes,
             {"fetched_at": "2026-07-07T15:10:00+08:00", "status": "fresh", "source": "eastmoney_quote"},
         )), \
         patch.object(svc, "_read_watchlist", return_value=[{
             "code": "000656", "name": "金科股份", "zt_date": "2026-07-06", "ref_price": 1.43, "added_date": "2026-07-02", "seal_time": "92500", "break_count": "4", "zt_count": "1", "consecutive": "3"
         }]), \
         patch.object(svc, "_fetch_principal_capital_map", return_value={}), \
         patch.object(svc, "count_zt_30days", return_value=1, create=True), \
         patch.object(svc, "DataCollectorAgent") as collector_cls, \
         patch.object(svc, "EventEngine") as event_engine_cls, \
         patch.object(svc, "SignalEngineAgent") as engine_cls, \
         patch("backend.agents.layer4_audit.AuditAgent") as audit_cls, \
         patch.object(svc, "RecommendationAgent") as recom_cls, \
         patch.object(svc, "send_data_integrity_alert", return_value=True, create=True) as send_alert:
        collector = collector_cls.return_value
        collector.collect_watchlist_quotes = AsyncMock(return_value=quotes)
        collector.collect_historical_batch = AsyncMock(return_value=historical)
        event_engine = event_engine_cls.return_value
        event_engine.collect_daily_events = AsyncMock(return_value=[])
        event_engine.match_stock_events = AsyncMock(return_value={})
        engine_cls.return_value.evaluate.return_value = [stock]
        audit_cls.return_value.audit_batch.return_value = {
            "stocks": [],
            "pass_rate": 100.0,
            "downgraded": 0,
        }
        recom_cls.return_value.execute = recom_execute

        result = asyncio.run(svc.run_full_pipeline(target_date=date(2026, 7, 7), dry_run=False))

    assert result["status"] == "blocked_stale_data"
    assert result["block_reason"] == [
        {
            "asset": "zt_pool",
            "expected_date": "2026-07-07",
            "fetched_at": "2026-07-06T17:43:40+08:00",
            "status": "fresh",
        },
        {
            "asset": "index_snapshot",
            "expected_date": "2026-07-07",
            "fetched_at": "2026-07-06T17:44:56+08:00",
            "status": "fresh",
        },
    ]
    assert recom_execute.await_count == 1
    assert recom_execute.await_args.kwargs["dry_run"] is True
    assert send_alert.call_count == 1
    assert result["results"][0]["evidence"]["zt_fetched_at"] == "2026-07-06T17:43:40+08:00"
    assert result["results"][0]["evidence"]["quote_fetched_at"] == "2026-07-07T15:10:00+08:00"
    assert result["results"][0]["evidence"]["history_length"] == 2


def test_run_full_pipeline_keeps_normal_path_when_core_sources_are_valid():
    zt_pool = pd.DataFrame([
        {"代码": "000656", "名称": "金科股份", "最新价": 1.43, "涨跌幅": 10.0, "换手率": 6.93, "流通市值": 10849909080.0, "封板时间": 92500, "炸板次数": 4, "连板数": 3}
    ])
    quotes = pd.DataFrame([
        {"代码": "000656", "名称": "金科股份", "最新价": 1.35, "涨跌幅": -5.59, "换手率": 6.93, "量比": 4.53, "市盈率": -89.62, "总市值": 15141886234.0, "流通市值": 10849909080.0}
    ])
    historical = {
        "000656": pd.DataFrame([
            {"日期": "2026-07-06", "收盘": 1.43, "涨跌幅": 10.0},
            {"日期": "2026-07-07", "收盘": 1.35, "涨跌幅": -5.59},
        ])
    }
    stock = _scored_stock()
    recom_execute = AsyncMock(
        return_value={
            "total_scored": 1,
            "strong_buy": 0,
            "buy": 0,
            "watch": 1,
            "report_md": "/tmp/report.md",
            "report_html": "/tmp/report.html",
            "notified": True,
        }
    )

    with patch.object(svc, "get_effective_config", return_value={"config": {"strategy": {"trackingDays": 30}}}), \
         patch.object(svc, "get_factor_weights_decimal", return_value={}), \
         patch.object(svc, "_read_index_snapshot_with_cache", return_value=(
             {"value": 4041.24, "gain_pct": -0.06, "source": "东方财富", "fetched_at": "2026-07-07T15:10:00+08:00"},
             {"fetched_at": "2026-07-07T15:10:00+08:00", "status": "fresh", "source": "eastmoney"},
         )), \
         patch.object(svc, "_read_zt_pool_with_cache", return_value=(
             zt_pool,
             {"fetched_at": "2026-07-07T15:10:00+08:00", "status": "fresh", "source": "eastmoney_direct"},
         )), \
         patch.object(svc, "_read_quotes_with_cache", return_value=(
             quotes,
             {"fetched_at": "2026-07-07T15:10:00+08:00", "status": "fresh", "source": "eastmoney_quote"},
         )), \
         patch.object(svc, "_read_watchlist", return_value=[{
             "code": "000656", "name": "金科股份", "zt_date": "2026-07-06", "ref_price": 1.43, "added_date": "2026-07-02", "seal_time": "92500", "break_count": "4", "zt_count": "1", "consecutive": "3"
         }]), \
         patch.object(svc, "_fetch_principal_capital_map", return_value={}), \
         patch.object(svc, "count_zt_30days", return_value=1, create=True), \
         patch.object(svc, "DataCollectorAgent") as collector_cls, \
         patch.object(svc, "EventEngine") as event_engine_cls, \
         patch.object(svc, "SignalEngineAgent") as engine_cls, \
         patch("backend.agents.layer4_audit.AuditAgent") as audit_cls, \
         patch.object(svc, "RecommendationAgent") as recom_cls, \
         patch.object(svc, "send_data_integrity_alert", return_value=True, create=True) as send_alert:
        collector = collector_cls.return_value
        collector.collect_watchlist_quotes = AsyncMock(return_value=quotes)
        collector.collect_historical_batch = AsyncMock(return_value=historical)
        event_engine = event_engine_cls.return_value
        event_engine.collect_daily_events = AsyncMock(return_value=[])
        event_engine.match_stock_events = AsyncMock(return_value={})
        engine_cls.return_value.evaluate.return_value = [stock]
        audit_cls.return_value.audit_batch.return_value = {
            "stocks": [],
            "pass_rate": 100.0,
            "downgraded": 0,
        }
        recom_cls.return_value.execute = recom_execute

        result = asyncio.run(svc.run_full_pipeline(target_date=date(2026, 7, 7), dry_run=False))

    assert result.get("status") != "blocked_stale_data"
    assert send_alert.call_count == 0
    assert recom_execute.await_count == 1
    assert recom_execute.await_args.kwargs["dry_run"] is False
