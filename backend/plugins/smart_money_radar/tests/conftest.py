from datetime import datetime, timedelta, timezone

import pytest


BEIJING_TZ = timezone(timedelta(hours=8))


@pytest.fixture
def fixed_now():
    return datetime(2026, 8, 18, 10, 30, tzinfo=BEIJING_TZ)


@pytest.fixture
def fake_stock_payload():
    return {
        "code": "600001",
        "name": "测试股份",
        "quote": {
            "code": "600001",
            "price": 10.0,
            "last_close": 9.9,
            "amount": 12_000_000,
            "vol": 100_000,
            "b_vol": 70_000,
            "s_vol": 30_000,
            "bid1": 9.99,
            "bid2": 9.98,
            "bid3": 9.97,
            "bid4": 9.96,
            "bid5": 9.95,
            "ask1": 10.01,
            "ask2": 10.02,
            "ask3": 10.03,
            "ask4": 10.04,
            "ask5": 10.05,
            "bid_vol1": 2000,
            "bid_vol2": 2000,
            "bid_vol3": 2000,
            "bid_vol4": 2000,
            "bid_vol5": 2000,
            "ask_vol1": 800,
            "ask_vol2": 800,
            "ask_vol3": 800,
            "ask_vol4": 800,
            "ask_vol5": 800,
        },
        "txs": [
            {"time": "10:28", "price": 9.98, "vol": 100, "buyorsell": 0, "num": 1},
            {"time": "10:29", "price": 10.00, "vol": 80, "buyorsell": 1, "num": 2},
            {"time": "10:29", "price": 10.01, "vol": 60, "buyorsell": 0, "num": 3},
        ],
        "servertime": "10:30",
        "fetched_at": datetime(2026, 8, 18, 10, 30, tzinfo=BEIJING_TZ).isoformat(),
    }

