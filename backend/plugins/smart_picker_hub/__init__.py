"""22 智能选股聚合中枢插件入口：独立聚合 CLI + 18/19/20/21 同进程连跑（共享 K 线缓存）。"""
import asyncio
import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)


def register_router() -> APIRouter:
    from .router import router
    return router


def run_smart_picker_hub_cli(args):
    """仅聚合（要求四份策略快照已存在）。"""
    import asyncio
    from .service import run_smart_picker_hub_once
    return asyncio.run(run_smart_picker_hub_once(force=getattr(args, "force", False)))


async def _run_all(args):
    """18/19/20/21 + 聚合中枢顺序连跑：同进程共享 get_kline_cached，单策略失败不阻断。"""
    from backend.plugins.chip_scanner.service import run_chip_scanner_once
    from backend.plugins.pattern_scanner.service import run_pattern_scanner_once
    from backend.plugins.tech_indicators.service import run_tech_indicators_once
    from backend.plugins.trend_strength.service import run_trend_strength_once

    from .service import run_smart_picker_hub_once

    force = getattr(args, "force", False)
    workers = getattr(args, "max_kline_workers", None)
    results = {}
    for label, fn in (("tech", run_tech_indicators_once), ("trend", run_trend_strength_once),
                      ("pattern", run_pattern_scanner_once)):
        try:
            results[label] = await fn(force=force, max_kline_workers=workers)
        except Exception as exc:  # noqa: BLE001
            logger.exception("%s 扫描失败（不阻断）: %s", label, exc)
            results[label] = {"status": "error", "message": str(exc)}
    try:
        results["chip"] = await run_chip_scanner_once(force=force, max_kline_workers=workers)
    except Exception as exc:  # noqa: BLE001 本地专属，失败不阻断
        logger.warning("chip 扫描失败（本地专属，不阻断）: %s", exc)
        results["chip"] = {"status": "error", "message": str(exc)}
    try:
        results["hub"] = await run_smart_picker_hub_once(force=force)
    except Exception as exc:  # noqa: BLE001
        logger.exception("聚合中枢失败: %s", exc)
        results["hub"] = {"status": "error", "message": str(exc)}
    return results


def run_smart_picker_all_cli(args):
    """组合 CLI：18/19/20/21 + hub 一个进程跑完（K 线 0 重复拉取）。"""
    return asyncio.run(_run_all(args))
