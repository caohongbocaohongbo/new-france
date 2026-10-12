"""26 绩效模拟盘插件入口。"""
from fastapi import APIRouter


def register_router() -> APIRouter:
    from .router import router
    return router


def run_paper_settle_cli(args):
    from .service import run_settle_once
    return run_settle_once()
