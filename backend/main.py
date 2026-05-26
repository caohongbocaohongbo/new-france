"""New France — 尾盘涨停选股系统入口 (CLI + API)"""
import sys
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent

# 确保项目根目录在 Python path 中（修复 crontab 环境下的模块导入问题）
sys.path.insert(0, str(PROJECT_DIR))
os.chdir(str(PROJECT_DIR))

# 加载 .env 文件（修复 SMTP 密码无法读取的问题）
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_DIR / ".env")
except ImportError:
    pass

# FastAPI app 延迟加载，仅在 --serve 时需要
_app = None


def get_app():
    """延迟加载 FastAPI，避免 CLI 模式强依赖 fastapi/uvicorn"""
    global _app
    if _app is not None:
        return _app

    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
    from .api.router_screening import router as screening_router
    from .api.router_watchlist import router as watchlist_router
    from .api.router_events import router as events_router
    from .api.router_reports import router as reports_router
    from .api.router_system import router as system_router

    _app = FastAPI(
        title="New France — 尾盘涨停选股系统",
        version="1.0.0",
        description="A股尾盘涨停股监控与多因子推荐系统 API",
    )
    _app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _app.include_router(screening_router, prefix="/api/v1/screening", tags=["筛选"])
    _app.include_router(watchlist_router, prefix="/api/v1/watchlist", tags=["监控列表"])
    _app.include_router(events_router, prefix="/api/v1/events", tags=["事件"])
    _app.include_router(reports_router, prefix="/api/v1/reports", tags=["报告"])
    _app.include_router(system_router, prefix="/api/v1/system", tags=["系统"])

    # 托管前端静态文件
    frontend_dir = PROJECT_DIR / "frontend"
    if frontend_dir.is_dir():
        _app.mount("/css", StaticFiles(directory=frontend_dir / "css"), name="css")
        _app.mount("/js", StaticFiles(directory=frontend_dir / "js"), name="js")

    @_app.get("/")
    def root():
        from fastapi.responses import FileResponse
        index_path = PROJECT_DIR / "frontend" / "index.html"
        if index_path.is_file():
            return FileResponse(str(index_path))
        return {"service": "New France API", "version": "1.0.0"}

    return _app


def main():
    """CLI 入口"""
    import argparse
    import logging
    import asyncio
    from datetime import date

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    logger = logging.getLogger("new-france")

    parser = argparse.ArgumentParser(description="New France 涨停回撤选股系统")
    parser.add_argument("--dry-run", action="store_true", help="仅筛选，不发通知")
    parser.add_argument("--force", action="store_true", help="强制运行（跳过交易日检查）")
    parser.add_argument("--test-email", action="store_true", help="发送测试邮件")
    parser.add_argument("--serve", action="store_true", help="启动 API 服务")
    parser.add_argument("--init-db", action="store_true", help="初始化数据库")
    args = parser.parse_args()

    if args.init_db:
        from .db.database import init_db
        init_db()
        logger.info("数据库已初始化")
        return

    if args.serve:
        import uvicorn
        from config.settings import settings
        from .db.database import init_db

        # 确保数据库表已创建
        init_db()

        app = get_app()
        port = settings.api_port
        logger.info(f"启动 API 服务 0.0.0.0:{port}")
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
        return

    if args.test_email:
        from .agents.layer3_recommendation.notifier import test_email
        test_email()
        return

    # 默认：执行每日完整流程
    asyncio.run(_run_daily_pipeline(args, logger))


async def _run_daily_pipeline(args, logger):
    """完整的每日定时任务"""
    from datetime import datetime, timezone, timedelta
    BEIJING_TZ = timezone(timedelta(hours=8))

    logger.info("New France v1.0 每日流水线启动")
    today = datetime.now(BEIJING_TZ).date()
    weekday = today.weekday()

    if weekday >= 5 and not args.force:
        logger.info(f"今天是周{['一','二','三','四','五','六','日'][weekday]}，非交易日，退出")
        return

    # 1. 抓取今日涨停股池
    from .agents.layer1_data_collector.sources.eastmoney_zt import fetch_zt_pool
    from .agents.layer1_data_collector.sources.index_data import fetch_index_gain

    logger.info("[Step 1] 抓取涨停股池...")
    zt_pool = fetch_zt_pool()
    if zt_pool is None or zt_pool.empty:
        logger.error("无法获取涨停股池，退出")
        return

    index_gain = fetch_index_gain()
    logger.info(f"  涨停: {len(zt_pool)} 只, 上证: {index_gain:+.2f}%")

    # 2. 更新监控列表
    logger.info("[Step 2] 更新监控列表...")
    france_file = PROJECT_DIR / "data" / "france.md"
    today_str = today.strftime("%Y-%m-%d")

    from .services.watchlist_store import normalize_watchlist_file, write_watchlist

    existing_entries, duplicate_count = normalize_watchlist_file(france_file)
    if duplicate_count:
        logger.info(f"  已清理 {duplicate_count} 条重复监控记录")
    existing_codes = {entry["code"] for entry in existing_entries}

    new_entries = []
    updated_entries = 0  # 已存在但更新了当天涨停数据的记录数
    for _, row in zt_pool.iterrows():
        code = str(row["代码"]).strip().zfill(6)
        name = str(row["名称"]).strip()
        price = float(row["最新价"])
        if price <= 0:
            continue
        fbt = str(row.get("封板时间", 0))
        zbc = str(row.get("炸板次数", 0))
        lbc = str(row.get("连板数", 0))

        if code in existing_codes:
            # 更新已存在记录的封板时间/炸板次数/连板数（同一天多次涨停）
            for entry in existing_entries:
                if entry["code"] == code:
                    if entry.get("seal_time") in (None, "0", 0) and fbt not in (None, "0", 0):
                        entry["seal_time"] = fbt
                        updated_entries += 1
                    entry["consecutive"] = lbc
                    entry["break_count"] = zbc  # 炸板次数
                    # 更新为今日涨停的最新参考价和涨停日期
                    entry["ref_price"] = price
                    entry["zt_date"] = today_str
                    break
            continue

        new_entries.append({
            "code": code, "name": name,
            "zt_date": today_str, "ref_price": price,
            "added_date": today_str,
            "seal_time": fbt,
            "break_count": zbc,
            "zt_count": "0",
            "consecutive": lbc,
        })
        existing_codes.add(code)

    if updated_entries:
        logger.info(f"  更新 {updated_entries} 只已有监控股的封板时间")
    if new_entries:
        all_entries = existing_entries + new_entries
    else:
        all_entries = existing_entries

    # 更新所有监控股的30天涨停频率统计
    from .services.watchlist_store import count_zt_30days
    zt_count_updated = 0
    for entry in all_entries:
        freq = count_zt_30days(entry["code"])
        if int(entry.get("zt_count", "0")) != freq:
            entry["zt_count"] = str(freq)
            zt_count_updated += 1

    write_watchlist(all_entries, france_file)
    if new_entries:
        logger.info(f"  新增 {len(new_entries)} 只监控股票")
    else:
        logger.info("  无新增（全部已监控）")
    if zt_count_updated:
        logger.info(f"  更新 {zt_count_updated} 只涨停频率统计")

    # 3. 执行完整筛选流水线
    logger.info("[Step 3] 执行筛选流水线...")
    from .services.screening_service import run_full_pipeline
    import json
    result = await run_full_pipeline(target_date=today, dry_run=args.dry_run)

    # 写入 latest.json（CLI 模式也需要更新，供前端轮询）
    reports_dir = PROJECT_DIR / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "latest.json").write_text(
        json.dumps({"status": "completed", "date": today_str, **result}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    logger.info(f"  结果: STRONG_BUY={result['strong_buy']}, BUY={result['buy']}, WATCH={result['watch']}")
    if result.get("errors"):
        for e in result["errors"]:
            logger.warning(f"  ⚠ {e}")

    if result["total_scored"] == 0:
        logger.info("筛选无果恰是市场救你，应果断空仓")

    logger.info("New France v1.0 每日流水线完成")


if __name__ == "__main__":
    main()
