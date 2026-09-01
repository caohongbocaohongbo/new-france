"""四维共振信号插件入口。"""
from fastapi import APIRouter


def register_router() -> APIRouter:
    from .router import router
    return router


def run_resonance_cli(args):
    import asyncio
    from .service import run_resonance_once
    return asyncio.run(run_resonance_once(force=getattr(args, "force", False),
                                          max_kline_workers=getattr(args, "max_kline_workers", None)))
