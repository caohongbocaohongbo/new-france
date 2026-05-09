"""FastAPI 应用入口"""
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.router_screening import router as screening_router
from .api.router_watchlist import router as watchlist_router
from .api.router_events import router as events_router
from .api.router_reports import router as reports_router
from .api.router_system import router as system_router

app = FastAPI(
    title="New France — 尾盘涨停选股系统",
    version="1.0.0",
    description="A股尾盘涨停股监控与多因子推荐系统 API",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(screening_router, prefix="/api/v1/screening", tags=["筛选"])
app.include_router(watchlist_router, prefix="/api/v1/watchlist", tags=["监控列表"])
app.include_router(events_router, prefix="/api/v1/events", tags=["事件"])
app.include_router(reports_router, prefix="/api/v1/reports", tags=["报告"])
app.include_router(system_router, prefix="/api/v1/system", tags=["系统"])


@app.get("/")
def root():
    return {"service": "New France API", "version": "1.0.0"}


def main():
    """CLI 入口 — 运行每日筛选"""
    import argparse
    import logging
    import asyncio
    from datetime import date

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")
    logger = logging.getLogger("new-france")

    parser = argparse.ArgumentParser(description="New France 涨停回撤选股系统")
    parser.add_argument("--dry-run", action="store_true", help="仅筛选，不发通知")
    parser.add_argument("--force", action="store_true", help="强制运行")
    parser.add_argument("--test-email", action="store_true", help="测试邮件")
    parser.add_argument("--serve", action="store_true", help="启动 API 服务")
    parser.add_argument("--init-db", action="store_true", help="初始化数据库")
    args = parser.parse_args()

    if args.init_db:
        from backend.db.database import init_db
        init_db()
        logger.info("数据库已初始化")
        return

    if args.serve:
        import uvicorn
        logger.info("启动 API 服务 http://localhost:8000")
        uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
        return

    if args.test_email:
        from backend.agents.layer3_recommendation.notifier import test_email
        test_email()
        return

    logger.info("New France v1.0 启动")
    # TODO: 完整的每日运行流程 (main pipeline)
    logger.info("完成。使用 --serve 启动 API 服务，或在 Web UI 中手动触发筛选。")


if __name__ == "__main__":
    main()
