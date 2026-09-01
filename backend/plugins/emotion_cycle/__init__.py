"""情绪周期与连板梯队插件入口。"""
from fastapi import APIRouter


def register_router() -> APIRouter:
    from .router import router
    return router


def run_emotion_cli(args):
    from .service import run_emotion_once
    return run_emotion_once(force=getattr(args, "force", False))
