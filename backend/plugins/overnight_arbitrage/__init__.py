"""尾盘隔夜套利插件入口。"""
from fastapi import APIRouter


def register_router() -> APIRouter:
    """对外暴露的路由注册入口。"""
    from .router import router
    return router


def run_cli(args):
    """CLI 入口（被 backend.main 调用）。"""
    import asyncio
    from .service import run_overnight_arbitrage

    return asyncio.run(run_overnight_arbitrage(
        target_date=getattr(args, "target_date", None),
        dry_run=getattr(args, "dry_run", False),
    ))
