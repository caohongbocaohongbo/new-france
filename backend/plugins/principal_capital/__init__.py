"""主力资金双向监控插件入口。

设计原则：本插件不污染主项目代码。对主项目的唯一耦合点是 backend/main.py
里一行 include_router(register_router()) 注入与一处可选 CLI 调用。

如需迁移到新项目：
  1. 整目录 backend/plugins/principal_capital/ 复制过去
  2. 在新项目的 FastAPI 入口添加：
     from backend.plugins.principal_capital import register_router
     app.include_router(register_router(), prefix="/api/v1/principal-capital")
  3. 复制 frontend/js/plugins/principal_capital.js + index.html 新增菜单项
"""
from fastapi import APIRouter


def register_router() -> APIRouter:
    """对外暴露的路由注册入口。"""
    from .router import router
    return router


def run_scan_cli(args):
    """CLI 入口（被 backend.main 调用）。"""
    from .service import run_principal_capital_scan
    return run_principal_capital_scan(
        buy_threshold=getattr(args, "buy_threshold", 50.0),
        sell_threshold=getattr(args, "sell_threshold", 30.0),
        enable_verify=getattr(args, "enable_verify", False),
        dry_run=getattr(args, "dry_run", False),
        force=getattr(args, "force", False),
        execution_mode=getattr(args, "execution_mode", None),
        pipeline_mode=getattr(args, "pipeline_mode", None),
        owner_id=getattr(args, "owner_id", None),
    )


def run_finalize_cli(args):
    """午间/收盘摘要 finalizer CLI 入口。"""
    from .service import finalize_principal_capital_session
    return finalize_principal_capital_session(
        session=getattr(args, "session", "am"),
        execution_mode=getattr(args, "execution_mode", None),
        owner_id=getattr(args, "owner_id", None),
    )
