"""28 低位竞价雷达常驻调度：09:15<=now<09:25 采集，now>=09:25 最终确认，定时消费 Outbox。"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from backend.api.router_system import trading_session_status
from .collector import TencentAuctionSource
from .config import BEIJING_TZ, CONFIG
from .service import finalize_once, run_auction_poll_once
from .store import AuctionStore

logger = logging.getLogger(__name__)


def auction_phase(now):
    """当前应执行阶段：pre(now<9:15 预热) | collect(9:15<=now<9:25) | finalize(now>=9:25)。"""
    auction_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    auction_end = now.replace(hour=9, minute=25, second=0, microsecond=0)
    if auction_start <= now < auction_end:
        return "collect"
    if now >= auction_end:
        return "finalize"
    return "pre"


def _prepare_frozen(now):
    from .pool import freeze_pool, prepare_low_pool
    pool = prepare_low_pool(now.date(), allow_regenerate=True)
    if pool.status != "completed":
        return None, pool
    frozen = freeze_pool(now.date())
    return frozen, pool


def _safe_dispatch():
    try:
        from .outbox import dispatch_outbox
        return dispatch_outbox()
    except Exception as exc:  # noqa: BLE001
        logger.warning("outbox dispatch 失败: %s", exc)
        return {"dispatched": 0, "error": str(exc)}


async def run_auction_radar_daemon(dry_run=False, force=False, max_cycles=None):
    """本地常驻：采集窗口与最终确认窗口用完整 datetime 边界；异常可重试。"""
    store = AuctionStore()
    source = TencentAuctionSource()
    frozen = None
    finalize_done = False
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        cycles += 1
        now = datetime.now(BEIJING_TZ)
        session = trading_session_status(now)
        try:
            if not session.get("is_trading_day"):
                _safe_dispatch()
                await asyncio.sleep(60)
                continue

            phase = auction_phase(now)
            if phase == "collect":
                # 采集窗口（不含 09:25:00）
                if frozen is None:
                    frozen, pool = _prepare_frozen(now)
                    if frozen is None or getattr(frozen, "status", "completed") != "completed":
                        logger.warning("竞价采集跳过：池不可用 %s", getattr(frozen, "reason", ""))
                        await asyncio.sleep(30)
                        continue
                    store.frozen_pool = frozen
                await run_auction_poll_once(now, store, source, frozen)
                await asyncio.sleep(max(1, int(CONFIG.get("base_poll_s", 5))))
            elif phase == "finalize":
                # 最终确认窗口：finalize 异常不置位，下一轮重试
                if frozen is not None and not finalize_done:
                    try:
                        await finalize_once(now.date().isoformat(), CONFIG["strategy_version"],
                                            store, source, now, dry_run=dry_run)
                        finalize_done = True
                    except Exception:  # noqa: BLE001
                        logger.exception("finalize 异常，将重试")
                _safe_dispatch()
                await asyncio.sleep(5)
            else:  # pre：预热并短轮询，避免 09:15 起采延迟
                frozen, _pool = _prepare_frozen(now)
                store.frozen_pool = frozen if frozen else None
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            source.disconnect()
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("低位竞价雷达单轮异常: %s", exc)
            await asyncio.sleep(30)
    source.disconnect()
    return {"status": "stopped", "cycles": cycles}
