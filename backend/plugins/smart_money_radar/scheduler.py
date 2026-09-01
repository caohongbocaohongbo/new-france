"""smart_money_radar 常驻调度、交易时段分支和掉线看门狗。"""
import asyncio
import logging
from datetime import datetime, timedelta

from backend.api.router_system import trading_session_status
from . import auction as _auction
from .config import BEIJING_TZ, CONFIG
from .notifier import build_and_send
from .service import _STORE, run_radar_once
from .sources.tdx_source import TdxPool

logger = logging.getLogger(__name__)


def sleep_seconds_for_session(session: str, now: datetime) -> int:
    if session == "pre_open": return 30
    if session == "midday_break": return 60
    if session == "trading": return max(1, int(CONFIG.get("poll_interval_s", 4)))
    target = now.replace(hour=9, minute=25, second=0, microsecond=0)
    if target <= now: target += timedelta(days=1)
    return max(60, int((target - now).total_seconds()))


class RadarWatchdog:
    def __init__(self, threshold: int, alert_fn):
        self.threshold = max(1, int(threshold)); self.alert_fn = alert_fn
        self.failures = 0; self.alerted = False

    def record_success(self):
        self.failures = 0; self.alerted = False

    def record_failure(self):
        self.failures += 1
        if self.failures >= self.threshold and not self.alerted:
            self.alerted = True; self.alert_fn()


async def run_radar_daemon(dry_run=False, force=False, store=None,
                           pool=None,
                           session_fn=trading_session_status,
                           now_fn=lambda: datetime.now(BEIJING_TZ),
                           run_once_fn=run_radar_once,
                           sleep_fn=asyncio.sleep, max_cycles=None):
    store = store or _STORE
    pool = pool or TdxPool()
    watchdog = RadarWatchdog(CONFIG.get("failure_threshold", 5), lambda: _send_watchdog_alert(now_fn()))
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        cycles += 1; now = now_fn(); session = session_fn(now); name = session.get("session", "non_trading_day")
        try:
            if force or session.get("is_trading_hours"):
                result = await run_once_fn(now=now, dry_run=dry_run, force=force, store=store, pool=pool)
                failed = result.get("status") == "error" or (
                    bool(result.get("errors")) and result.get("pool_count", 0) > 0
                    and result.get("pool_count", 0) == len(result.get("errors", []))
                )
                (watchdog.record_failure if failed else watchdog.record_success)()
            elif name == "pre_open":
                await asyncio.to_thread(pool.connect)
                # 13 集合竞价：09:15-09:25 启用竞价采集，其余 pre_open 时段维持预热
                if _auction.is_in_auction_window(now, CONFIG):
                    await _run_auction_poll(pool, store, now)
                watchdog.record_success()
            elif name == "midday_break":
                store.gc(now, CONFIG.get("bar_cache_ttl_s", 45)); watchdog.record_success()
            else:
                store.reset(); watchdog.record_success()
            await sleep_fn(sleep_seconds_for_session("trading" if session.get("is_trading_hours") else name, now))
        except asyncio.CancelledError:
            pool.disconnect()
            raise
        except Exception as exc:
            logger.exception("盘中雷达单轮异常: %s", exc); watchdog.record_failure(); await sleep_fn(30)
    pool.disconnect()


async def _run_auction_poll(pool, store, now: datetime) -> None:
    # 竞价采集只在 pre_open 分支调用，与 trading 分支的主雷达循环不共享 pool 调用，无并发竞争
    """竞价时窗内采集观察池虚拟开盘价/匹配量。"""
    try:
        from .service import load_watch_pool, run_auction_poll_once

        watch_pool = load_watch_pool(now=now)
        if watch_pool:
            await run_auction_poll_once(pool, store, now, watch_pool)
    except Exception as exc:  # noqa: BLE001
        logger.warning("竞价采集异常: %s", exc)


def _send_watchdog_alert(now: datetime):
    try:
        build_and_send([{"code": "--", "name": "TDX", "stage": "雷达掉线", "smart_money_score": 0, "launch_score": 0}], CONFIG.get("radar_source", "local"), now)
    except Exception:
        logger.exception("发送雷达掉线告警失败")
