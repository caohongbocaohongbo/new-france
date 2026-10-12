"""26 事件驱动雷达插件入口。"""
from fastapi import APIRouter


def register_router() -> APIRouter:
    from .router import router
    return router


def run_event_scan_cli(args):
    from .service import run_scan_once
    return run_scan_once()
