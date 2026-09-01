"""涨停封单强度与开板回封监测插件入口。"""
from fastapi import APIRouter


def register_router() -> APIRouter:
    from .router import router
    return router


def run_zt_seal_cli(args):
    from .service import run_zt_seal_once
    return run_zt_seal_once(force=getattr(args, "force", False))
