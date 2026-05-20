"""
筛选流水线服务 — 串联三级 Agent 完整流程
DataCollector → SignalEngine → RecommendationAgent
"""
import asyncio
import logging
from datetime import date
from typing import Dict, List, Optional

import pandas as pd

from ..agents.layer1_data_collector.agent import DataCollectorAgent
from ..agents.layer2_signal_engine.agent import SignalEngineAgent
from ..agents.layer3_recommendation.agent import RecommendationAgent
from ..events.engine import EventEngine
from .watchlist_store import FRANCE_FILE, parse_watchlist

logger = logging.getLogger(__name__)

def _read_watchlist() -> List[Dict]:
    """从 france.md 读取监控列表"""
    return parse_watchlist(FRANCE_FILE)


async def run_full_pipeline(
    target_date: Optional[date] = None,
    drop_min: float = 3.0,
    drop_max: float = 10.0,
    vol_min: float = 1.0,
    vol_max: float = 5.0,
    turnover_min: float = 5.0,
    turnover_max: float = 10.0,
    mc_min: float = 50.0,
    mc_max: float = 200.0,
    pe_max: float = 50.0,
    dry_run: bool = False,
) -> dict:
    """
    完整的每日筛选流水线

    Returns:
        {
            "total_scored": int,
            "strong_buy": int, "buy": int, "watch": int,
            "results": [ScoredStock dicts],
            "errors": [...],
        }
    """
    if target_date is None:
        target_date = date.today()

    errors = []
    logger.info("=" * 50)
    logger.info(f"New France 筛选流水线启动 — {target_date}")
    logger.info("=" * 50)

    # ---- Layer 1: 数据采集 ----
    logger.info("[Layer 1] 数据采集...")
    collector = DataCollectorAgent()

    # 获取指数涨幅
    from ..agents.layer1_data_collector.sources.index_data import fetch_index_gain
    from ..agents.layer1_data_collector.sources.eastmoney_zt import fetch_zt_pool
    from ..agents.layer1_data_collector.sources.eastmoney_quote import fetch_stock_quotes
    index_gain = fetch_index_gain()
    logger.info(f"  上证指数涨幅: {index_gain:+.2f}%")

    # 获取当日涨停股池
    zt_pool = fetch_zt_pool()
    zt_list = []  # 涨停股列表（用于邮件通知）
    if zt_pool is not None and not zt_pool.empty:
        zt_codes = zt_pool["代码"].tolist()
        logger.info(f"  涨停股池: {len(zt_codes)} 只")
        # 拉取涨停股的实时行情（获取量比、PE等补充字段）
        zt_quotes = fetch_stock_quotes(zt_codes)
        zt_quote_map = {}
        if not zt_quotes.empty:
            for _, row in zt_quotes.iterrows():
                zt_quote_map[row["代码"]] = row
        # 构建涨停股列表
        for _, zt in zt_pool.iterrows():
            code = zt["代码"]
            q = zt_quote_map.get(code, {})
            zt_list.append({
                "code": code,
                "name": zt["名称"],
                "price": zt.get("最新价", 0) if not isinstance(q, dict) else zt["最新价"],
                "change_pct": zt.get("涨跌幅", 0),
                "turnover": q.get("换手率") if not isinstance(q, dict) else zt.get("换手率", 0),
                "vol_ratio": q.get("量比") if not isinstance(q, dict) else None,
                "pe": q.get("市盈率") if not isinstance(q, dict) else None,
                "mcap": zt.get("流通市值", 0),
                "seal_time": zt.get("封板时间", 0),
                "break_count": zt.get("炸板次数", 0),
                "consecutive": zt.get("连板数", 0),
            })
    else:
        logger.info("  涨停股池: 无数据")

    # 读取监控列表
    watchlist = _read_watchlist()
    if not watchlist:
        return {
            "total_scored": 0, "strong_buy": 0, "buy": 0, "watch": 0,
            "results": [], "index_gain": index_gain, "zt_list": zt_list,
            "errors": ["监控列表为空，请先添加股票"],
        }

    logger.info(f"  监控列表: {len(watchlist)} 只")

    # 拉取实时行情 + 历史K线 + 事件采集（并行，无数据依赖）
    codes = [e["code"] for e in watchlist]
    event_engine = EventEngine()

    quotes, historical, events = await asyncio.gather(
        collector.collect_watchlist_quotes(codes),
        collector.collect_historical_batch(codes),
        event_engine.collect_daily_events(target_date),
    )
    logger.info(f"  实时行情: {len(quotes)} 只")

    # ---- 事件匹配 ----
    watchlist_names = [e["name"] for e in watchlist]
    event_map = await event_engine.match_stock_events(events, codes, watchlist_names)

    # ---- 回撤检测 ----
    logger.info("[Layer 2] 回撤检测...")
    quote_map = {}
    for _, row in quotes.iterrows():
        quote_map[row["代码"]] = row

    candidates = []
    for e in watchlist:
        code = e["code"]
        if code not in quote_map:
            continue
        q = quote_map[code]
        current_price = q["最新价"]
        if current_price is None or current_price <= 0:
            continue

        drop_pct = (current_price - e["ref_price"]) / e["ref_price"] * 100
        abs_drop = abs(drop_pct)

        if abs_drop < drop_min or abs_drop > drop_max:
            continue

        # 量比/换手率/PE/市值 快速筛选
        vr = q.get("量比")
        to = q.get("换手率")
        pe = q.get("市盈率")
        mc = q.get("流通市值")

        if vr is not None and vr > 0 and not (vol_min <= vr <= vol_max):
            continue
        if to is not None and to > 0 and not (turnover_min <= to <= turnover_max):
            continue
        if pe is not None and pe > 0 and pe > pe_max:
            continue
        if mc is not None and mc > 0:
            mc_yi = mc / 1e8
            if not (mc_min <= mc_yi <= mc_max):
                continue

        candidates.append({
            "code": code,
            "name": e["name"],
            "zt_date": e["zt_date"],
            "ref_price": e["ref_price"],
            "current_price": current_price,
            "drop_pct": round(drop_pct, 2),
            "涨跌幅": q["涨跌幅"],
            "换手率": q["换手率"],
            "市盈率": q["市盈率"],
            "量比": q["量比"],
            "总市值": q["总市值"],
            "流通市值": q["流通市值"],
            "封板时间": 0,
            "炸板次数": 0,
            "涨停频率": 0,
        })

    logger.info(f"  回撤检测 + 快速筛选后: {len(candidates)} 只候选")

    # ---- Layer 2: 多因子评分 ----
    logger.info("[Layer 2] 多因子评分...")
    engine = SignalEngineAgent()
    scored = engine.evaluate(candidates, quotes, historical, index_gain, event_map)

    # ---- Layer 3: 生成报告 ----
    logger.info("[Layer 3] 生成报告...")
    recom = RecommendationAgent()
    summary = await recom.execute(scored, target_date, index_gain,
                                  zt_list=zt_list, dry_run=dry_run)

    # ---- 构建响应 ----
    results = []
    for s in scored:
        factor_list = {}
        for key, r in s.factor_scores.items():
            factor_list[key] = {
                "name": r.name,
                "score": r.score,
                "weight": r.weight,
                "detail": r.detail,
                "passed": r.passed,
            }
        results.append({
            "rank": s.rank,
            "code": s.code,
            "name": s.name,
            "zt_date": s.zt_date,
            "ref_price": s.ref_price,
            "current_price": s.current_price,
            "drop_pct": s.drop_pct,
            "total_score": s.total_score,
            "event_impact": s.event_impact,
            "adjusted_score": s.adjusted_score,
            "recommendation": s.recommendation,
            "factors": factor_list,
        })

    return {
        "date": target_date.strftime("%Y-%m-%d"),
        "index_gain": index_gain,
        "zt_list": zt_list,
        "total_scored": summary["total_scored"],
        "strong_buy": summary["strong_buy"],
        "buy": summary["buy"],
        "watch": summary["watch"],
        "results": results,
        "report_md": summary.get("report_md"),
        "report_html": summary.get("report_html"),
        "errors": errors,
    }
