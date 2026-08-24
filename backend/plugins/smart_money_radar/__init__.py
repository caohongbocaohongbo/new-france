"""smart_money_radar 盘中雷达插件入口。"""
from fastapi import APIRouter


def register_router() -> APIRouter:
    """对外暴露的路由注册入口。"""
    from .router import router
    return router


def run_radar_once_cli(args):
    """执行单轮盘中雷达。"""
    import asyncio

    from .service import run_radar_once

    return asyncio.run(run_radar_once(
        dry_run=getattr(args, "dry_run", False),
        force=getattr(args, "force", False),
    ))


def run_radar_daemon_cli(args):
    """启动常驻雷达循环。Phase 4 完整强化，Phase 1 提供入口占位。"""
    import asyncio

    from .scheduler import run_radar_daemon

    return asyncio.run(run_radar_daemon(
        dry_run=getattr(args, "dry_run", False),
        force=getattr(args, "force", False),
    ))


def run_verify_tdx_cli(args):
    """验证 TDX 连接。"""
    del args
    from .sources.tdx_source import verify_tdx

    return verify_tdx()

