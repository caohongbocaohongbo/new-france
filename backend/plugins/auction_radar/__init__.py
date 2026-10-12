"""28 低位竞价雷达插件入口。"""
from fastapi import APIRouter


def register_router() -> APIRouter:
    from .router import router
    return router


def run_auction_radar_cli(args):
    import asyncio
    from .service import run_auction_radar_once
    return asyncio.run(run_auction_radar_once(
        dry_run=getattr(args, "dry_run", False),
        force=getattr(args, "force", False),
    ))


def run_auction_radar_daemon_cli(args):
    import asyncio
    from .scheduler import run_auction_radar_daemon
    return asyncio.run(run_auction_radar_daemon(
        dry_run=getattr(args, "dry_run", False),
        force=getattr(args, "force", False),
    ))


def run_auction_radar_review_cli(args):
    from .outcomes import append_outcome_labels
    from .config import CONFIG
    return append_outcome_labels("review", "v1", target_date=getattr(args, "summary_trade_date", None))
