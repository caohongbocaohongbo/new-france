import asyncio
from datetime import datetime, timezone, timedelta

from backend.plugins.smart_money_radar import scheduler


TZ = timezone(timedelta(hours=8))


def test_watchdog_alerts_once_at_failure_threshold_and_recovers():
    alerts = []
    watchdog = scheduler.RadarWatchdog(2, lambda: alerts.append("alert"))
    watchdog.record_failure()
    watchdog.record_failure()
    watchdog.record_failure()
    assert alerts == ["alert"]
    watchdog.record_success()
    watchdog.record_failure()
    watchdog.record_failure()
    assert alerts == ["alert", "alert"]


def test_closed_session_sleeps_until_next_0925():
    now = datetime(2026, 8, 18, 16, 0, tzinfo=TZ)
    seconds = scheduler.sleep_seconds_for_session("closed", now)
    assert seconds == int((datetime(2026, 8, 19, 9, 25, tzinfo=TZ) - now).total_seconds())


def test_scheduler_handles_midday_and_closed_once():
    sessions = iter([
        {"session": "midday_break", "is_trading_hours": False},
        {"session": "closed", "is_trading_hours": False},
    ])
    class Store:
        def __init__(self):
            self.gc_calls = 0
            self.reset_calls = 0
        def gc(self, *args, **kwargs):
            self.gc_calls += 1
        def reset(self):
            self.reset_calls += 1
    store = Store()
    sleeps = []
    async def one_sleep(value):
        sleeps.append(value)
        if len(sleeps) >= 2:
            raise asyncio.CancelledError
    async def one_cycle(**kwargs):
        raise AssertionError("非交易时段不应采集")
    try:
        asyncio.run(scheduler.run_radar_daemon(
            store=store, session_fn=lambda now: next(sessions), now_fn=lambda: datetime(2026, 8, 18, 12, 0, tzinfo=TZ),
            run_once_fn=one_cycle, sleep_fn=one_sleep, max_cycles=2,
        ))
    except asyncio.CancelledError:
        pass
    assert store.gc_calls == 1
    assert store.reset_calls == 1


def test_pre_open_warms_tdx_connection():
    sessions = iter([{"session": "pre_open", "is_trading_hours": False}])
    class Store:
        def gc(self, *args, **kwargs): pass
        def reset(self): pass
    class Pool:
        def __init__(self): self.connect_calls = 0; self.disconnect_calls = 0
        def connect(self): self.connect_calls += 1
        def disconnect(self): self.disconnect_calls += 1
    pool = Pool()
    async def stop(_): raise asyncio.CancelledError
    try:
        asyncio.run(scheduler.run_radar_daemon(
            store=Store(), pool=pool, session_fn=lambda now: next(sessions),
            now_fn=lambda: datetime(2026, 8, 18, 9, 20, tzinfo=TZ),
            sleep_fn=stop, max_cycles=1,
        ))
    except asyncio.CancelledError:
        pass
    assert pool.connect_calls == 1
    assert pool.disconnect_calls == 1
