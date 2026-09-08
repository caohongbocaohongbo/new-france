"""筹码集中度与获利盘选股插件入口。"""
from fastapi import APIRouter


def register_router() -> APIRouter:
    from .router import router
    return router


def run_chip_scanner_cli(args):
    import asyncio
    from .service import run_chip_scanner_once
    return asyncio.run(run_chip_scanner_once(force=getattr(args, "force", False),
                                             max_kline_workers=getattr(args, "max_kline_workers", None)))
