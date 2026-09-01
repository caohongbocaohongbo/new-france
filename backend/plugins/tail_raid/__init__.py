"""全A尾盘异动抢筹雷达插件入口。"""
from fastapi import APIRouter


def register_router() -> APIRouter:
    from .router import router
    return router


def run_tail_raid_cli(args):
    from .service import run_tail_raid_once
    return run_tail_raid_once(force=getattr(args, "force", False))
