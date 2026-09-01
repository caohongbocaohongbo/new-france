"""低位涨停选股器插件入口。"""
from fastapi import APIRouter


def register_router() -> APIRouter:
    from .router import router
    return router


def run_low_position_cli(args):
    import asyncio
    from .service import run_low_position_once
    return asyncio.run(run_low_position_once(force=getattr(args, "force", False),
                                             max_kline_workers=getattr(args, "max_kline_workers", None)))
