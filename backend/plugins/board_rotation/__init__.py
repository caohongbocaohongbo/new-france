"""板块轮动与题材热度共振插件入口。"""
from fastapi import APIRouter


def register_router() -> APIRouter:
    from .router import router
    return router


def run_board_cli(args):
    from .service import run_board_once
    return run_board_once(force=getattr(args, "force", False))
