"""真实 Level-2 升级路径插件入口（M1 调研先行，采集端后置）。"""
from fastapi import APIRouter


def register_router() -> APIRouter:
    from .router import router
    return router


def run_l2_cli(args):
    from .service import run_l2_research
    return run_l2_research(force=getattr(args, "force", False))
