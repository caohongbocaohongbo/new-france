"""分价成交与主力成本带插件入口。"""
from fastapi import APIRouter


def register_router() -> APIRouter:
    from .router import router
    return router


def run_volume_profile_cli(args):
    from .service import run_volume_profile_once
    return run_volume_profile_once(force=getattr(args, "force", False))
