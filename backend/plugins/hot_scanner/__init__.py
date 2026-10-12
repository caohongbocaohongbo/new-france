"""26 舆情热度聚合插件入口。"""
from fastapi import APIRouter


def register_router() -> APIRouter:
    from .router import router
    return router


def run_hot_scan_cli(args):
    from .service import run_scan_once
    return run_scan_once()
