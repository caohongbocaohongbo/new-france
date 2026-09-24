"""决策质量闭环回归（§12.4 第 1 步）：固化 P0/P1 反例。"""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

BEIJING_TZ = timezone(timedelta(hours=8))


def test_snapshots_partial_return_does_not_claim_coverage(monkeypatch, tmp_path):
    """离线返回一只旧报价不得声称两只覆盖：received_codes 而非 requested_codes。"""
    from backend.services.data_backend import snapshots

    monkeypatch.setattr(snapshots, "DATA_BACKEND_DIR", tmp_path)
    monkeypatch.setattr(snapshots, "REPORT_DATA_BACKEND_DIR", tmp_path / "reports")
    monkeypatch.setattr(snapshots, "_MEMORY_CACHE", {})
    monkeypatch.setattr(snapshots, "_now", lambda: datetime(2026, 7, 6, 10, 0, tzinfo=BEIJING_TZ))

    fetched = pd.DataFrame([{"代码": "600001", "名称": "A", "最新价": 10.0}])
    result, meta = snapshots.read_quotes(["600001", "600002"], fetcher=lambda codes: fetched)
    assert result["代码"].tolist() == ["600001"]
    assert meta["received_count"] == 1
    assert meta["missing_count"] == 1


def test_snapshots_unknown_source_time_rows_are_not_fresh(monkeypatch, tmp_path):
    """全部行 source_time 未知/degraded 时，不得标 fresh。"""
    from backend.services.data_backend import snapshots

    monkeypatch.setattr(snapshots, "DATA_BACKEND_DIR", tmp_path)
    monkeypatch.setattr(snapshots, "REPORT_DATA_BACKEND_DIR", tmp_path / "reports")
    monkeypatch.setattr(snapshots, "_MEMORY_CACHE", {})
    monkeypatch.setattr(snapshots, "_now", lambda: datetime(2026, 7, 6, 10, 0, tzinfo=BEIJING_TZ))

    fetched = pd.DataFrame([{"代码": "600001", "名称": "A", "最新价": 10.0, "source_time": None, "degraded": True}])
    _, meta = snapshots.read_quotes(["600001"], fetcher=lambda codes: fetched)
    assert meta["status"] == "degraded"


def test_eastmoney_legacy_fetch_time_is_fresh():
    """东财兜底行以采集时刻为行情时间，不再强制降级（修复单只停牌股污染整份快照导致阻断）。"""
    from backend.agents.layer1_data_collector.sources import eastmoney_quote

    data = {"data": {"diff": [{"f12": "600519", "f14": "贵州茅台", "f2": 1300.0, "f3": 1.5, "f5": 1000, "f6": 130000000}]}}
    rows = eastmoney_quote._parse_response(data)
    assert rows and rows[0]["source_time"] is not None
    assert rows[0]["degraded"] is False


def test_kline_short_cache_does_not_serve_longer_window(monkeypatch):
    """请求130根返回30根后，再请求100根不得命中30根缓存。"""
    import backend.plugins.common as common

    calls = {"n": 0}

    def fetcher(code, days):
        calls["n"] += 1
        return pd.DataFrame({"日期": [f"2026-06-{i:02d}" for i in range(1, min(days, 30) + 1)],
                             "收盘": list(range(1, min(days, 30) + 1))})

    common.kline_cache_clear()
    common.get_kline_cached("600519", 130, fetcher=fetcher)  # 实际只返回 30 根
    common.get_kline_cached("600519", 100, fetcher=fetcher)  # 不得命中短缓存
    assert calls["n"] == 2
    common.kline_cache_clear()


def test_kline_cache_isolation_by_fetcher(monkeypatch):
    """不同取数器（不同复权/源能力）不得串缓存。"""
    import backend.plugins.common as common

    def a(code, days):
        return pd.DataFrame({"日期": ["2026-06-01"], "收盘": [1.0]})

    def b(code, days):
        return pd.DataFrame({"日期": ["2026-06-01"], "收盘": [2.0]})

    common.kline_cache_clear()
    common.get_kline_cached("600519", 10, fetcher=a)
    out = common.get_kline_cached("600519", 10, fetcher=b)
    assert out["收盘"].tolist() == [2.0]
    common.kline_cache_clear()

def _quote_row(code="600001", price=12.0, **extra):
    row = {
        "代码": code, "名称": "测试", "最新价": price, "涨跌幅": 6.0, "成交额": 3e8,
        "换手率": 5.0, "量比": 2.0, "流通市值": 5e9,
    }
    row.update(extra)
    return row


def test_stale_quote_does_not_produce_buy():
    from datetime import date as _date
    from backend.plugins.overnight_arbitrage.service import build_overnight_decision

    df = pd.DataFrame([_quote_row(is_stale=True, source_time="2026-07-03T14:50:00+08:00")])
    decision = build_overnight_decision(df, target_date=_date(2026, 7, 6))
    assert decision["buy_count"] == 0
    assert decision["data_quality"]["status"] != "complete"


def test_degraded_fallback_quote_does_not_produce_buy():
    from datetime import date as _date
    from backend.plugins.overnight_arbitrage.service import build_overnight_decision

    df = pd.DataFrame([_quote_row(degraded=True)])
    decision = build_overnight_decision(df, target_date=_date(2026, 7, 6))
    assert decision["buy_count"] == 0
    assert decision["data_quality"]["status"] == "partial"


def test_sourced_unknown_naive_and_future_source_time_do_not_produce_buy():
    from datetime import date as _date
    from backend.plugins.overnight_arbitrage.service import build_overnight_decision

    zt = pd.DataFrame([
        {"代码": "600001", "封板时间": 143000, "炸板次数": 0, "连板数": 1},
        {"代码": "600002", "封板时间": 143000, "炸板次数": 0, "连板数": 1},
        {"代码": "600003", "封板时间": 143000, "炸板次数": 0, "连板数": 1},
    ])
    df = pd.DataFrame([
        _quote_row("600001", 数据源="sina_all_a", source_time=None, 涨跌幅=9.0, 成交额=8e8, 换手率=12.0, 量比=5.0),
        _quote_row("600002", source="tencent", source_time="2026-07-06T14:43:00", 涨跌幅=9.0, 成交额=8e8, 换手率=12.0, 量比=5.0),
        _quote_row("600003", source="tencent", source_time="2026-07-06T14:43:10+08:00", 涨跌幅=9.0, 成交额=8e8, 换手率=12.0, 量比=5.0),
    ])

    decision = build_overnight_decision(
        df,
        zt_pool=zt,
        target_date=_date(2026, 7, 6),
        generated_at="2026-07-06 14:43:00",
    )

    assert decision["results"] == []
    qualities = {item["code"]: item["quality"] for item in decision["data_quality"]["removed"]}
    assert qualities["600001"] == "source_time_unknown"
    assert qualities["600002"] == "source_time_naive"
    assert qualities["600003"] == "source_time_future"


def test_yahoo5m_stale_timestamp_yields_no_strength(monkeypatch):
    import time as _time
    from backend.plugins.overnight_arbitrage.service import _fetch_yahoo_5m_strength

    now_ts = int(_time.time())
    stale_ts = now_ts - 60 * 60
    fake = {
        "chart": {"result": [{
            "timestamp": [stale_ts - 300, stale_ts, stale_ts + 300, stale_ts + 600],
            "indicators": {"quote": [{"close": [10, 10.1, 10.2, 10.3],
                                       "high": [10.05, 10.15, 10.25, 10.35],
                                       "low": [9.9, 10.0, 10.1, 10.2]}]},
        }]},
    }

    class Resp:
        def raise_for_status(self): pass
        def json(self): return fake

    monkeypatch.setattr("requests.get", lambda *a, **k: Resp())
    out = _fetch_yahoo_5m_strength(["600001"])
    assert "600001" not in out

def test_eastmoney_snapshot_records_coverage_metadata():
    import requests
    from backend.plugins.overnight_arbitrage import service as oa

    def fake_get(url, params=None, headers=None, timeout=None):
        class R:
            def raise_for_status(self): pass
            def json(self):
                # total=5 但只回 2 行 → truncated
                return {"data": {"total": 5, "diff": [
                    {"f12": "600001", "f14": "A", "f2": 10.0, "f3": 1.0, "f15": 10.1, "f5": 100, "f6": 1e8, "f8": 1.0, "f9": 10.0, "f10": 1.0, "f20": 1e10, "f21": 8e9},
                    {"f12": "600002", "f14": "B", "f2": 10.0, "f3": 1.0, "f15": 10.1, "f5": 100, "f6": 1e8, "f8": 1.0, "f9": 10.0, "f10": 1.0, "f20": 1e10, "f21": 8e9},
                ]}}
        return R()

    monkeypatch = None
    import pytest
    # 用 monkeypatch 注入 requests.get


def _eastmoney_coverage_test(monkeypatch):
    from backend.plugins.overnight_arbitrage.service import _eastmoney_all_a_snapshot

    def fake_get(url, params=None, headers=None, timeout=None):
        class R:
            def raise_for_status(self): pass
            def json(self):
                rows = [
                    {"f12": "600001", "f14": "A", "f2": 10.0, "f3": 1.0, "f15": 10.1, "f5": 100, "f6": 1e8, "f8": 1.0, "f9": 10.0, "f10": 1.0, "f20": 1e10, "f21": 8e9},
                    {"f12": "600002", "f14": "B", "f2": 10.0, "f3": 1.0, "f15": 10.1, "f5": 100, "f6": 1e8, "f8": 1.0, "f9": 10.0, "f10": 1.0, "f20": 1e10, "f21": 8e9},
                ]
                diff = rows if params.get("pn") == 1 else []
                return {"data": {"total": 5, "diff": diff}}
        return R()

    monkeypatch.setattr("requests.get", fake_get)
    df = _eastmoney_all_a_snapshot()
    cov = df.attrs["coverage"]
    assert cov["universe_total"] >= 10  # 至少两个 fs 分组各 total=5
    assert cov["received"] == 2  # 去重后实际收到
    assert cov["truncated"] is True


def test_eastmoney_snapshot_records_coverage_metadata(monkeypatch):
    _eastmoney_coverage_test(monkeypatch)

def test_candidate_refiner_replaces_candidate_rows(monkeypatch):
    from datetime import date as _date
    import asyncio
    from backend.plugins.overnight_arbitrage import service as oa

    quotes = pd.DataFrame([
        _quote_row("600001", price=10.0, 换手率=1.0),
        _quote_row("600002", price=11.0, 涨跌幅=9.0, 成交额=8e8, 换手率=12.0, 量比=5.0),
    ])
    calls = {}

    def fake_refiner(df, codes):
        calls["codes"] = list(codes)
        row = _quote_row("600002", price=11.0, 涨跌幅=9.0, 成交额=8e8, 换手率=12.0, 量比=5.0)
        row["source_time"] = "2026-07-06T14:50:00+08:00"
        out = df[df["代码"] != "600002"].copy()
        out = pd.concat([out, pd.DataFrame([row])], ignore_index=True)
        return out

    async def run():
        return await oa.run_overnight_arbitrage(
            target_date=_date(2026, 7, 6),
            quote_fetcher=lambda: quotes,
            zt_fetcher=lambda d: pd.DataFrame(),
            minute_fetcher=lambda c: {},
            candidate_refiner=fake_refiner,
            dry_run=True,
            current_time=__import__("datetime").datetime(2026, 7, 6, 14, 45, tzinfo=oa.BEIJING_TZ),
        )

    result = asyncio.run(run())
    assert calls.get("codes") == ["600002"]  # 粗筛后唯一进入结果的候选


def test_missing_volume_ratio_seed_can_be_rescued_by_refiner(monkeypatch):
    from datetime import date as _date
    import asyncio
    from backend.plugins.overnight_arbitrage import service as oa

    rough = pd.DataFrame([
        _quote_row("600002", 涨跌幅=9.0, 成交额=8e8, 换手率=12.0, 量比=None),
    ])
    zt = pd.DataFrame([{"代码": "600002", "封板时间": 143000, "炸板次数": 0, "连板数": 1}])

    def fake_refiner(df, codes):
        out = pd.DataFrame([
            _quote_row("600002", 涨跌幅=9.0, 成交额=8e8, 换手率=12.0, 量比=5.0,
                       source="tencent", source_time="2026-07-06T14:44:30+08:00"),
        ])
        out.attrs["refined_codes"] = ["600002"]
        return out

    monkeypatch.setattr(oa, "write_overnight_report", lambda payload: None)
    result = asyncio.run(oa.run_overnight_arbitrage(
        target_date=_date(2026, 7, 6),
        quote_fetcher=lambda: rough,
        zt_fetcher=lambda d: zt,
        minute_fetcher=lambda c: {},
        candidate_refiner=fake_refiner,
        dry_run=True,
        current_time=datetime(2026, 7, 6, 14, 45, tzinfo=oa.BEIJING_TZ),
    ))

    assert [item["code"] for item in result["results"]] == ["600002"]


def test_refiner_failure_does_not_fallback_to_coarse_buy(monkeypatch):
    from datetime import date as _date
    import asyncio
    from backend.plugins.overnight_arbitrage import service as oa

    rough = pd.DataFrame([
        _quote_row("600002", 涨跌幅=9.0, 成交额=8e8, 换手率=12.0, 量比=5.0,
                   source_time="2026-07-06T14:44:30+08:00"),
    ])
    zt = pd.DataFrame([{"代码": "600002", "封板时间": 143000, "炸板次数": 0, "连板数": 1}])

    monkeypatch.setattr(oa, "write_overnight_report", lambda payload: None)
    result = asyncio.run(oa.run_overnight_arbitrage(
        target_date=_date(2026, 7, 6),
        quote_fetcher=lambda: rough,
        zt_fetcher=lambda d: zt,
        minute_fetcher=lambda c: {},
        candidate_refiner=lambda df, codes: (_ for _ in ()).throw(RuntimeError("timeout")),
        dry_run=True,
        current_time=datetime(2026, 7, 6, 14, 45, tzinfo=oa.BEIJING_TZ),
    ))

    assert result["results"] == []
    assert result["data_quality"]["removed"][0]["quality"] == "degraded_or_stale"


def test_completion_after_1455_blocks_notification(monkeypatch):
    from datetime import date as _date
    import asyncio
    from backend.plugins.overnight_arbitrage import service as oa

    class FakeDateTime(datetime):
        calls = 0

        @classmethod
        def now(cls, tz=None):
            cls.calls += 1
            minute = 54 if cls.calls == 1 else 56
            return datetime(2026, 7, 6, 14, minute, tzinfo=tz)

    zt = pd.DataFrame([{"代码": "600002", "封板时间": 143000, "炸板次数": 0, "连板数": 1}])
    quotes = pd.DataFrame([
        _quote_row("600002", 涨跌幅=9.0, 成交额=8e8, 换手率=12.0, 量比=5.0,
                   source_time="2026-07-06T14:54:00+08:00"),
    ])

    monkeypatch.setattr(oa, "datetime", FakeDateTime)
    monkeypatch.setattr(oa, "write_overnight_report", lambda payload: None)
    monkeypatch.setattr(oa, "update_overnight_history", lambda payload: {
        "status": "completed",
        "records": [],
        "total_stocks": 0,
        "total_recommendations": 0,
        "updated_at": "2026-07-06 14:56:00",
    })
    result = asyncio.run(oa.run_overnight_arbitrage(
        target_date=_date(2026, 7, 6),
        quote_fetcher=lambda: quotes,
        zt_fetcher=lambda d: zt,
        minute_fetcher=lambda c: {},
        dry_run=False,
        current_time=datetime(2026, 7, 6, 14, 54, tzinfo=oa.BEIJING_TZ),
    ))

    assert "completed_after_valid_window" in result["notification"]["blocked_reasons"]
    assert result["notification"]["sent"] is False


def test_refine_quotes_preserves_coverage_attrs():
    from backend.plugins.overnight_arbitrage.service import _refine_quotes_with_tencent

    quotes = pd.DataFrame([_quote_row("600001"), _quote_row("600002")])
    quotes.attrs["coverage"] = {"source": "eastmoney_all_a", "universe_total": 100, "received": 2, "truncated": True}
    monkeypatch = None
    # 注入假 refiner（monkeypatch 网络函数）


def _refine_coverage_test(monkeypatch):
    from backend.plugins.overnight_arbitrage.service import _refine_quotes_with_tencent
    import backend.agents.layer1_data_collector.sources.eastmoney_quote as eq

    def fake_fetch(codes):
        return pd.DataFrame([_quote_row("600001", 换手率=9.0)])

    monkeypatch.setattr(eq, "fetch_tencent_quotes_for_codes", fake_fetch)
    quotes = pd.DataFrame([_quote_row("600001", 换手率=1.0), _quote_row("600002", 换手率=8.0)])
    quotes.attrs["coverage"] = {"source": "eastmoney_all_a", "universe_total": 100, "received": 2, "truncated": True}
    out = _refine_quotes_with_tencent(quotes, ["600001"])
    assert out.attrs.get("coverage", {}).get("source") == "eastmoney_all_a"
    assert out.attrs.get("refined_codes") == ["600001"]


def test_refine_quotes_preserves_coverage_attrs(monkeypatch):
    _refine_coverage_test(monkeypatch)

def test_snapshot_atomic_publish_same_version_and_inconsistent_detected(tmp_path, monkeypatch):
    import json
    import backend.services.data_backend.snapshots as snapshots

    monkeypatch.setattr(snapshots, "DATA_BACKEND_DIR", tmp_path)
    monkeypatch.setattr(snapshots, "REPORT_DATA_BACKEND_DIR", tmp_path / "reports")
    monkeypatch.setattr(snapshots, "_MEMORY_CACHE", {})
    monkeypatch.setattr(snapshots, "_now", lambda: __import__("datetime").datetime(2026, 7, 6, 10, 0, tzinfo=snapshots.BEIJING_TZ))

    snapshots._write_local_snapshot("quotes", {"records": [{"代码": "600001"}]})
    canonical = json.loads((tmp_path / "quotes.json").read_text(encoding="utf-8"))
    mirror = json.loads((tmp_path / "reports" / "quotes.json").read_text(encoding="utf-8"))
    assert canonical["snapshot_version"] == mirror["snapshot_version"]
    # 人为破坏 mirror 版本 → 读取以 canonical 为准并标记不一致
    mirror["snapshot_version"] = "bogus"
    (tmp_path / "reports" / "quotes.json").write_text(json.dumps(mirror), encoding="utf-8")
    monkeypatch.setattr(snapshots, "_MEMORY_CACHE", {})
    payload = snapshots._read_local_snapshot("quotes")
    assert payload["_batch_inconsistent"] is True
    assert payload["snapshot_version"] == canonical["snapshot_version"]




def test_kline_write_through_is_gated_and_best_effort(monkeypatch):
    import pandas as pd
    import backend.plugins.common as common

    written = []

    def fake_upsert(code, df, **kwargs):
        written.append((str(code), len(df), kwargs.get("adjustment"), kwargs.get("source")))

    import backend.services.data_backend.bars_store as bs
    monkeypatch.setattr(bs, "upsert_daily_bars", fake_upsert)

    def fetcher(code, days):
        return pd.DataFrame({"日期": ["2026-06-01", "2026-06-02"], "收盘": [1.0, 2.0]})

    common.kline_cache_clear()
    monkeypatch.delenv("KLINE_STORE_WRITE_ENABLED", raising=False)
    common.get_kline_cached("600519", 10, fetcher=fetcher)
    assert written == []
    monkeypatch.setenv("KLINE_STORE_WRITE_ENABLED", "1")
    common.get_kline_cached("600519", 10, fetcher=fetcher)
    assert len(written) == 1
    assert written[0][0] == "600519" and written[0][1] == 2 and written[0][2] == "raw"
    common.kline_cache_clear()




def test_zt_events_availability_surfaced():
    from datetime import date as _date
    import pandas as pd
    from backend.plugins.overnight_arbitrage.service import build_overnight_decision

    df = pd.DataFrame([_quote_row("600002", 涨跌幅=9.0, 成交额=8e8, 换手率=12.0, 量比=5.0)])
    # 无涨停池 → capabilities.zt_events_available=False，结果项标 unavailable
    d = build_overnight_decision(df, target_date=_date(2026, 7, 6))
    assert d["capabilities"]["zt_events_available"] is False
    assert d["results"] == []
    assert d["data_quality"]["removed"][0]["unavailable_required_fields"] == ["zt_events"]
    # 有涨停池 → True，且不再标 unavailable
    zt = pd.DataFrame([{"代码": "600002", "封板时间": 143000, "炸板次数": 0, "连板数": 1}])
    d2 = build_overnight_decision(df, zt_pool=zt, target_date=_date(2026, 7, 6))
    assert d2["capabilities"]["zt_events_available"] is True
    assert all("zt_events" not in it.get("unavailable_required_fields", []) for it in d2["results"] or [])




def test_emotion_unavailable_on_zt_fetch_failure(monkeypatch):
    from datetime import date as _date
    import backend.plugins.emotion_cycle.service as emo
    import backend.agents.layer1_data_collector.sources.eastmoney_zt as ztsrc

    def boom(target):
        raise RuntimeError("东财封禁")

    monkeypatch.setattr(ztsrc, "fetch_zt_pool", boom)
    monkeypatch.setattr(emo, "is_trading_day", lambda d: True)
    monkeypatch.setattr(emo, "write_snapshot", lambda name, payload: payload)

    out = emo.run_emotion_once(target_date=_date(2026, 7, 6))
    assert out["status"] == "unavailable_required_fields"
    assert out["unavailable_required_fields"] == ["zt_events"]


def test_zt_seal_unavailable_on_zt_fetch_failure(monkeypatch):
    from datetime import date as _date
    import backend.plugins.zt_seal.service as seal
    import backend.agents.layer1_data_collector.sources.eastmoney_zt as ztsrc

    def boom(target):
        raise RuntimeError("东财封禁")

    monkeypatch.setattr(ztsrc, "fetch_zt_pool", boom)
    monkeypatch.setattr(seal, "is_trading_day", lambda d: True)
    monkeypatch.setattr(seal, "write_snapshot", lambda name, payload: payload)

    out = seal.run_zt_seal_once(target_date=_date(2026, 7, 6))
    assert out["status"] == "unavailable_required_fields"
    assert out["unavailable_required_fields"] == ["zt_events"]




def test_fallback_supplements_missing_codes_from_backup(monkeypatch):
    import pandas as pd
    import backend.plugins.overnight_arbitrage.service as oa

    def primary():
        df = pd.DataFrame([
            _quote_row("600001", 换手率=8.0),
            _quote_row("600002", 换手率=8.0),
        ])
        df.attrs["coverage"] = {"source": "eastmoney_all_a", "universe_total": 100, "received": 2, "truncated": True}
        return df

    def backup():
        df = pd.DataFrame([
            _quote_row("600002", 换手率=9.0),
            _quote_row("600003", 换手率=7.0),
        ])
        df.attrs["coverage"] = {"source": "sina_all_a", "universe_total": None, "received": 2, "truncated": False}
        return df

    monkeypatch.setattr(oa, "_sina_all_a_snapshot", backup)
    quotes, statuses, errors = oa._fetch_quotes_with_fallbacks(primary, zt_pool=None)
    codes = set(quotes["代码"])
    assert codes == {"600001", "600002", "600003"}
    assert any(s["source"] == "sina_all_a" for s in statuses)


def test_kline_adjustment_version_invalidation_on_change(monkeypatch):
    import pandas as pd
    import backend.plugins.common as common
    import backend.services.data_backend.bars_store as bs

    calls = {"deleted": 0, "upserted": 0}
    monkeypatch.setenv("KLINE_STORE_WRITE_ENABLED", "1")
    monkeypatch.setattr(bs, "current_version", lambda code, adj: "v1")

    def fake_delete(code, adj):
        calls["deleted"] += 1

    def fake_upsert(code, df, **kw):
        calls["upserted"] += 1

    monkeypatch.setattr(bs, "delete_adjustment", fake_delete)
    monkeypatch.setattr(bs, "upsert_daily_bars", fake_upsert)

    df = pd.DataFrame({"日期": ["2026-06-01"], "收盘": [1.0]})
    df.attrs["adjustment"] = "qfq"
    df.attrs["adjustment_version"] = "v2"
    common._persist_kline_best_effort("600519", df)
    assert calls["deleted"] == 1
    assert calls["upserted"] == 1


def test_compute_limit_prices_limit_free_returns_unknown():
    from backend.agents.layer1_data_collector.sources.zt_contract import compute_limit_prices, classify_zt_basic

    assert compute_limit_prices("10.0", "600001", "", None, limit_free=True) == (None, None, None)
    out = classify_zt_basic({"代码": "600001", "名称": "新股", "最新价": 10.0, "最高价": 10.5, "涨停价": 11.0, "limit_free": True})
    assert out["state"] == "unknown" and "price_limit_free" in out["unknown_reasons"]


def test_kline_return_carries_coverage_attrs():
    import backend.plugins.common as common

    def fetcher(code, days):
        return __import__("pandas").DataFrame({"日期": ["2026-06-01", "2026-06-02"], "收盘": [1.0, 2.0]})

    common.kline_cache_clear()
    out = common.get_kline_cached("600519", 130, fetcher=fetcher)
    cov = out.attrs.get("kline_coverage")
    assert cov["rows"] == 2
    assert cov["first"] == "2026-06-01" and cov["last"] == "2026-06-02"
    assert cov["short"] is True and cov["reason"] == "short"
    common.kline_cache_clear()
