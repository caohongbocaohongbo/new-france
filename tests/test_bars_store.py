"""日K持久化（§12.4 第3步）离线单测：不污染生产库，用 tmp SQLite 引擎。"""
import sqlite3

import pandas as pd
import pytest
from sqlalchemy import create_engine


@pytest.fixture
def bars(monkeypatch, tmp_path):
    import backend.services.data_backend.bars_store as store

    eng = create_engine(f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setattr(store, "engine", eng)
    return store


def _df(dates, closes):
    return pd.DataFrame({"日期": dates, "开盘": [c * 0.99 for c in closes],
                         "收盘": closes, "最高": [c * 1.01 for c in closes],
                         "最低": [c * 0.98 for c in closes], "成交量": [100] * len(closes),
                         "成交额": [1000] * len(closes)})


def test_upsert_then_read_and_idempotent(bars):
    bars.upsert_daily_bars("600001", _df(["2026-07-06", "2026-07-07"], [10.0, 10.5]),
                           adjustment="raw", source="test", is_final=True)
    out = bars.read_daily_bars("600001", "2026-07-06", "2026-07-07", "raw")
    assert out["close"].tolist() == [10.0, 10.5]
    assert out["is_final"].tolist() == [1, 1]
    # 幂等 upsert：同 (code,date,adjustment) 更新而非重复
    bars.upsert_daily_bars("600001", _df(["2026-07-07"], [11.0]), adjustment="raw", source="test")
    out = bars.read_daily_bars("600001", "2026-07-06", "2026-07-07", "raw")
    assert len(out) == 2
    assert out[out["trade_date"] == "2026-07-07"]["close"].tolist() == [11.0]


def test_missing_dates_uses_trading_calendar(bars, monkeypatch):
    import backend.services.data_backend.bars_store as store

    monkeypatch.setattr(store, "trading_days_between_dates",
                        lambda s, e: [__import__("datetime").date.fromisoformat(d)
                                      for d in ["2026-07-06", "2026-07-07", "2026-07-08"]])
    monkeypatch.setattr(store, "is_trading_day", lambda d: True)
    bars.upsert_daily_bars("600001", _df(["2026-07-06"], [10.0]), adjustment="raw")
    miss = bars.missing_dates("600001", "2026-07-06", "2026-07-08", "raw")
    assert miss == ["2026-07-07", "2026-07-08"]


def test_delete_adjustment_invalidates_version(bars):
    bars.upsert_daily_bars("600001", _df(["2026-07-06"], [10.0]), adjustment="qfq", adjustment_version="v1")
    assert len(bars.read_daily_bars("600001", adjustment="qfq")) == 1
    assert bars.delete_adjustment("600001", "qfq") == 1
    assert bars.read_daily_bars("600001", adjustment="qfq").empty


def test_backup_and_verify(bars, tmp_path):
    bars.upsert_daily_bars("600001", _df(["2026-07-06"], [10.0]), adjustment="raw")
    target = tmp_path / "backup.db"
    bars.backup(target)
    assert target.exists()
    assert bars.verify_backup(target) is True
