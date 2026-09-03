"""经典技术指标选股插件入口。"""
from fastapi import APIRouter


def register_router() -> APIRouter:
    from .router import router
    return router


def run_tech_indicators_cli(args):
    import asyncio
    from .service import run_tech_indicators_once
    return asyncio.run(run_tech_indicators_once(force=getattr(args, "force", False),
                                                max_kline_workers=getattr(args, "max_kline_workers", None)))
