"""26 条件盯盘插件入口。"""
from fastapi import APIRouter


def register_router() -> APIRouter:
    from .router import router
    return router
