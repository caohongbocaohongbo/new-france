from datetime import timedelta

from backend.plugins.smart_money_radar.store import RadarStore


def test_store_deduplicates_transactions_by_num(fixed_now):
    store = RadarStore(max_quotes=5)
    first = [
        {"num": 1, "time": "10:28", "price": 10.0, "vol": 10, "buyorsell": 0},
        {"num": 2, "time": "10:29", "price": 10.1, "vol": 20, "buyorsell": 1},
    ]
    second = [
        {"num": 2, "time": "10:29", "price": 10.1, "vol": 20, "buyorsell": 1},
        {"num": 3, "time": "10:30", "price": 10.2, "vol": 30, "buyorsell": 0},
    ]

    assert len(store.update_transactions("600001", first, fixed_now)) == 2
    assert len(store.update_transactions("600001", second, fixed_now)) == 1
    assert store.last_num["600001"] == 3


def test_store_quote_window_has_maxlen(fixed_now):
    store = RadarStore(max_quotes=2)
    store.add_quote("600001", {"price": 10}, fixed_now)
    store.add_quote("600001", {"price": 10.1}, fixed_now)
    store.add_quote("600001", {"price": 10.2}, fixed_now)

    assert len(store.quotes["600001"]) == 2
    assert store.quotes["600001"][0]["quote"]["price"] == 10.1


def test_store_reset_clears_intraday_state(fixed_now):
    store = RadarStore(max_quotes=5)
    store.add_quote("600001", {"price": 10}, fixed_now)
    store.update_transactions("600001", [{"num": 1, "time": "10:28"}], fixed_now)

    store.reset()

    assert not store.quotes
    assert not store.last_num
    assert not store.minute_buckets


def test_store_caches_bars_until_ttl_expires(fixed_now):
    store = RadarStore(max_quotes=5)
    bars = [{"close": 10, "vol": 100}]
    store.cache_bars("600001", 8, bars, fixed_now)
    assert store.get_cached_bars("600001", 8, fixed_now, ttl_seconds=45) == bars
    expired = fixed_now + timedelta(seconds=46)
    assert store.get_cached_bars("600001", 8, expired, ttl_seconds=45) is None


def test_store_caches_finance_once_per_trading_day(fixed_now):
    store = RadarStore(max_quotes=5)
    finance = {"liutongguben": 1000000}
    store.cache_finance("600001", finance, fixed_now)
    assert store.get_cached_finance("600001", fixed_now) == finance
    next_day = fixed_now + timedelta(days=1)
    assert store.get_cached_finance("600001", next_day) is None


def test_store_fund_series_is_bounded_and_reset():
    store = RadarStore(max_quotes=2, max_fund_points=2)
    store.add_fund_point("600001", {"time": "09:31", "vol": 10})
    store.add_fund_point("600001", {"time": "09:32", "vol": 20})
    store.add_fund_point("600001", {"time": "09:33", "vol": 30})
    assert len(store.fund_series["600001"]) == 2
    store.reset()
    assert not store.fund_series


def test_store_resets_when_beijing_trading_date_changes(fixed_now):
    store = RadarStore()
    store.ensure_date(fixed_now)
    store.add_quote("600001", {"price": 10}, fixed_now)
    store.last_num["600001"] = 99
    store.ensure_date(fixed_now + timedelta(days=1))
    assert not store.quotes
    assert not store.last_num


def test_store_accepts_next_day_transaction_numbers_starting_at_one(fixed_now):
    store = RadarStore()
    store.ensure_date(fixed_now)
    store.update_transactions("600001", [{"num": 9876, "time": "14:59"}], fixed_now)

    next_day = fixed_now + timedelta(days=1)
    store.ensure_date(next_day)
    fresh = store.update_transactions("600001", [{"num": 1, "time": "09:31"}], next_day)

    assert len(fresh) == 1
    assert fresh[0]["num"] == 1
    assert store.last_num["600001"] == 1


def test_store_gc_removes_expired_bar_cache(fixed_now):
    store = RadarStore()
    store.cache_bars("600001", 8, [{"close": 10}], fixed_now - timedelta(seconds=100))
    store.gc(fixed_now, ttl_seconds=45)
    assert not store.bar_cache
