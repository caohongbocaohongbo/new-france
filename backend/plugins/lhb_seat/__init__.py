"""龙虎榜与席位画像插件入口。"""
from fastapi import APIRouter


def register_router() -> APIRouter:
    from .router import router
    return router


def run_lhb_cli(args):
    from .service import run_lhb_once
    return run_lhb_once(force=getattr(args, "force", False))
