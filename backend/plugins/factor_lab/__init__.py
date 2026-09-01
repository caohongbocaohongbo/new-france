"""因子实验室与绩效归因插件入口。"""
from fastapi import APIRouter


def register_router() -> APIRouter:
    from .router import router
    return router


def run_factor_lab_cli(args):
    from .service import run_factor_lab
    return run_factor_lab(force=getattr(args, "force", False))
