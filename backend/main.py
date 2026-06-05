"""New France — 尾盘涨停选股系统入口 (CLI + API)"""
import sys
import os
import math
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


def _empty_optional_statuses():
    """读取可选数据源状态失败时返回稳定结构。"""
    return {"sources": {}, "updated_at": None}


def _json_safe(value):
    """清理 NaN/Infinity，保证 latest.json 可被 FastAPI 直接响应。"""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _run_optional_sources(logger):
    """刷新旁路数据源；失败只记录状态，不影响每日筛选主链路。"""
    optional_statuses = _empty_optional_statuses()
    logger.info("[Optional] 刷新旁路数据源...")
    try:
        from .services.national_team_service import refresh_national_team_data
        from .services.optional_source_health import (
            get_optional_source_statuses,
            record_optional_source_result,
        )
        nt_result = refresh_national_team_data(max_pages_per_filter=1, page_size=30)
        record_optional_source_result("national_team", nt_result)
        if nt_result.get("ok"):
            logger.info(
                "  国家队动向: 持仓%s条, 变动%s条, 事件%s条",
                nt_result.get("holding_count", 0),
                nt_result.get("change_count", 0),
                nt_result.get("event_count", 0),
            )
        else:
            logger.warning(f"  国家队动向刷新失败: {nt_result.get('source_status', {}).get('errors')}")
        optional_statuses = get_optional_source_statuses()
    except Exception as e:
        logger.warning(f"  旁路数据源刷新异常: {e}")
        try:
            from .services.optional_source_health import (
                get_optional_source_statuses,
                record_optional_source_result,
            )
            record_optional_source_result(
                "national_team",
                {
                    "ok": False,
                    "source_status": {
                        "source": "东方财富股东分析",
                        "errors": [str(e)],
                    },
                },
            )
            optional_statuses = get_optional_source_statuses()
        except Exception as inner:
            logger.warning(f"  旁路数据源状态写入失败: {inner}")
    return optional_statuses


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
    from .api.router_config import router as config_router
    from .api.router_national_team import router as national_team_router
    from .api.router_overnight_arbitrage import router as overnight_arbitrage_router

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
    _app.include_router(config_router, prefix="/api/v1/config", tags=["配置"])
    _app.include_router(national_team_router, prefix="/api/v1/national-team", tags=["国家队动向"])
    _app.include_router(overnight_arbitrage_router, prefix="/api/v1/overnight-arbitrage", tags=["尾盘隔夜套利"])

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
    parser.add_argument("--run-overnight-arbitrage", action="store_true",
                        help="运行尾盘隔夜套利 14:43 决策任务")
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
        from .db.database import init_db
        init_db()
        from .agents.layer3_recommendation.notifier import test_email
        test_email()
        return

    if args.run_overnight_arbitrage:
        asyncio.run(_run_overnight_arbitrage_cli(args, logger))
        return

    # 默认：执行每日完整流程
    asyncio.run(_run_daily_pipeline(args, logger))


async def _run_overnight_arbitrage_cli(args, logger):
    """尾盘隔夜套利独立定时任务。"""
    from datetime import datetime, timezone, timedelta
    from .db.database import init_db
    from .services.overnight_arbitrage_service import run_overnight_arbitrage

    BEIJING_TZ = timezone(timedelta(hours=8))
    init_db()
    today = datetime.now(BEIJING_TZ).date()
    weekday = today.weekday()
    if weekday >= 5 and not args.force:
        logger.info(f"今天是周{['一','二','三','四','五','六','日'][weekday]}，非交易日，尾盘套利退出")
        return

    logger.info("尾盘隔夜套利任务启动")
    result = await run_overnight_arbitrage(target_date=today, dry_run=args.dry_run)
    logger.info(
        "尾盘隔夜套利完成: status=%s, BUY=%s, WATCH=%s, 扫描=%s",
        result.get("status", "completed"),
        result.get("buy_count", 0),
        result.get("watch_count", 0),
        result.get("total_scanned", 0),
    )


async def _run_daily_pipeline(args, logger):
    """完整的每日定时任务"""
    from datetime import datetime, timezone, timedelta
    from .db.database import init_db
    BEIJING_TZ = timezone(timedelta(hours=8))

    init_db()
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
    from .services.runtime_config import get_effective_config, resolve_screening_params
    runtime_config = get_effective_config()["config"]
    screening_params = resolve_screening_params()
    tracking_days = int(runtime_config["strategy"].get("trackingDays", 30))

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

    # 更新所有监控股在配置周期内的涨停频率统计
    from .services.watchlist_store import count_zt_30days
    zt_count_updated = 0
    for entry in all_entries:
        freq = count_zt_30days(entry["code"], days=tracking_days)
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

    # 3. 刷新旁路数据源。旁路源只写健康状态，是否进入邮件/Dashboard 由配置或 promotion PR 控制。
    optional_statuses = _run_optional_sources(logger)

    # 4. 执行完整筛选流水线（核心链路）
    logger.info("[Step 4] 执行筛选流水线...")
    from .services.screening_service import run_full_pipeline
    import json
    result = await run_full_pipeline(target_date=today, dry_run=args.dry_run, **screening_params)

    # 写入 latest.json（CLI 模式也需要更新，供前端轮询）
    reports_dir = PROJECT_DIR / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "latest.json").write_text(
        json.dumps(
            _json_safe({
                "status": "completed",
                "date": today_str,
                **result,
                "optional_sources": optional_statuses,
            }),
            ensure_ascii=False,
            indent=2,
        ),
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
