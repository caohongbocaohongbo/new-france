"""
筛选流水线服务 — 串联三级 Agent 完整流程
DataCollector → SignalEngine → RecommendationAgent
"""
import asyncio
import logging
import queue
import threading
from datetime import date
from typing import Dict, List, Optional

import pandas as pd

from ..agents.layer1_data_collector.agent import DataCollectorAgent
from ..agents.layer2_signal_engine.agent import SignalEngineAgent
from ..agents.layer3_recommendation.agent import RecommendationAgent
from ..agents.layer3_recommendation.notifier import send_data_integrity_alert
from ..events.engine import EventEngine
from .runtime_config import get_effective_config, get_factor_weights_decimal
from .watchlist_store import FRANCE_FILE, parse_watchlist
from .drawdown import build_cumulative_price_history_series, latest_cumulative_drawdown

logger = logging.getLogger(__name__)

# 主力资金插件可选, 失败不影响主链路
try:
    from backend.plugins.principal_capital.sources.multi_source import (
        get_cached_fund_flow as _pc_get_cached,
        fetch_market_fund_flow_resilient as _pc_fetch_resilient,
    )
    _HAS_PC_PLUGIN = True
except ImportError:
    _HAS_PC_PLUGIN = False


def _read_index_snapshot_with_cache() -> tuple[dict, dict]:
    """统一读取指数快照。"""
    from ..agents.layer1_data_collector.sources.index_data import fetch_index_snapshot
    from .data_backend.snapshots import read_index_snapshot

    snapshot, meta = read_index_snapshot(fetch_index_snapshot)
    return (snapshot or {}), meta


def _read_zt_pool_with_cache(target_date: Optional[date] = None) -> tuple[Optional[pd.DataFrame], dict]:
    """统一读取涨停池快照。"""
    from ..agents.layer1_data_collector.sources.eastmoney_zt import fetch_zt_pool
    from .data_backend.snapshots import read_zt_pool

    payload, meta = read_zt_pool(lambda: fetch_zt_pool(target_date))
    if payload is not None:
        payload.attrs["source_meta"] = meta
    return payload, meta


def _read_quotes_with_cache(codes: List[str]) -> tuple[Optional[pd.DataFrame], dict]:
    """统一读取实时行情快照。"""
    from ..agents.layer1_data_collector.sources.eastmoney_quote import fetch_stock_quotes
    from .data_backend.snapshots import read_quotes

    return read_quotes(codes, fetch_stock_quotes)

def _read_watchlist() -> List[Dict]:
    """从 france.md 读取监控列表"""
    return parse_watchlist(FRANCE_FILE)


def _fetch_index_snapshot() -> dict:
    """获取上证指数真实快照，失败时保留空值而不是伪造点位。"""
    snapshot, _meta = _read_index_snapshot_with_cache()
    return snapshot


def _validate_core_sources_for_date(target_date: date, core_source_meta: dict) -> list[dict]:
    """校验核心行情源是否全部为目标交易日 fresh 数据。"""
    problems = []
    target_str = target_date.strftime("%Y-%m-%d")
    for asset, meta in (core_source_meta or {}).items():
        meta = meta or {}
        fetched_at = meta.get("fetched_at")
        status = meta.get("status")
        fetched_day = str(fetched_at or "")[:10]
        if not fetched_at or fetched_day != target_str or status not in {"fresh"}:
            problems.append({
                "asset": asset,
                "expected_date": target_str,
                "fetched_at": fetched_at,
                "status": status,
            })
    return problems


def _build_stock_evidence(
    stock,
    historical: Dict[str, pd.DataFrame],
    quotes_meta: dict,
    zt_meta: dict,
    index_meta: dict,
) -> dict:
    history = historical.get(stock.code)
    return {
        "entry_reason": "watchlist_drawdown",
        "zt_date": stock.zt_date,
        "added_date": (stock.extra or {}).get("added_date"),
        "quote_source": (quotes_meta or {}).get("source"),
        "quote_fetched_at": (quotes_meta or {}).get("fetched_at"),
        "zt_source": (zt_meta or {}).get("source"),
        "zt_fetched_at": (zt_meta or {}).get("fetched_at"),
        "index_fetched_at": (index_meta or {}).get("fetched_at"),
        "history_length": len(history) if history is not None else 0,
    }


async def _fetch_principal_capital_map(target_date) -> dict:
    """获取主力资金占比映射 {code: main_inflow_ratio}。"""
    if not _HAS_PC_PLUGIN:
        return {}
    try:
        df, age = _pc_get_cached(max_age_seconds=600)
        if df is not None and not df.empty:
            logger.info(f"  主力资金: 命中缓存 (age={age}s, {len(df)} 只)")
            return df.set_index("code")["main_inflow_ratio"].to_dict()

        logger.info("  主力资金: 缓存过期, 现拉全市场...")
        result_queue: "queue.Queue" = queue.Queue(maxsize=1)

        def _worker() -> None:
            try:
                result_queue.put(("ok", _pc_fetch_resilient()))
            except Exception as exc:
                result_queue.put(("error", exc))

        thread = threading.Thread(target=_worker, name="principal-capital-fetch", daemon=True)
        thread.start()
        try:
            state, payload = await asyncio.to_thread(result_queue.get, True, 20)
        except queue.Empty:
            logger.warning("  主力资金: 现拉超时，改用过期缓存兜底")
            state, payload = "timeout", None

        if state == "error":
            raise payload

        if state == "ok":
            df, status = payload
        else:
            df, status = None, {}
        if df is not None and not df.empty:
            logger.info(
                f"  主力资金: 现拉成功 ({len(df)} 只, source={status.get('active_source')})"
            )
            return df.set_index("code")["main_inflow_ratio"].to_dict()

        df, age = _pc_get_cached(max_age_seconds=24 * 3600)
        if df is not None and not df.empty:
            logger.warning(f"  主力资金: 全部数据源失败, 用过期缓存兜底 (age={age}s)")
            return df.set_index("code")["main_inflow_ratio"].to_dict()
    except Exception as exc:
        logger.warning(f"  主力资金数据获取失败: {exc}")
    return {}


def _build_price_history_series(hist: Optional[pd.DataFrame],
                                watch_date: str,
                                ref_price: float,
                                current_price: Optional[float] = None,
                                current_date: Optional[date] = None) -> List[Dict]:
    """生成从关注日起的收盘价和参考价回撤序列。"""
    return build_cumulative_price_history_series(
        hist,
        watch_date,
        ref_price,
        current_price=current_price,
        current_date=current_date,
        limit=12,
    )


def _build_watchlist_drawdown_summary(
    watchlist: List[Dict],
    quotes: pd.DataFrame,
    historical: Dict[str, pd.DataFrame],
    target_date: date,
    tracking_days: int,
    recommended_codes: set,
) -> Dict:
    """复用主筛选链路数据，生成监控列表真实回撤状态摘要。"""
    quote_map = {}
    if quotes is not None and not quotes.empty:
        for _, row in quotes.iterrows():
            quote_map[row["代码"]] = row

    counts = {"active": 0, "recommended": 0, "expired": 0, "waiting": 0, "unknown": 0}
    items = []
    for entry in watchlist:
        code = entry["code"]
        drop_pct = None
        try:
            days_since = (target_date - date.fromisoformat(entry["zt_date"])).days
        except Exception:
            days_since = 0
        if days_since > tracking_days:
            status = "expired"
        elif code in recommended_codes:
            status = "recommended"
        else:
            q = quote_map.get(code)
            current_price = q.get("最新价") if q is not None else None
            drop_pct = latest_cumulative_drawdown(
                historical.get(code),
                entry.get("added_date", entry["zt_date"]),
                entry["ref_price"],
                current_price=current_price,
                current_date=target_date,
            )
            if drop_pct is None:
                status = "unknown"
            elif drop_pct < 0:
                status = "active"
            else:
                status = "waiting"
        counts[status] += 1
        items.append({
            "code": code,
            "status": status,
            "drop_pct": round(drop_pct, 2) if drop_pct is not None else None,
        })

    return {
        "status_counts": counts,
        "items": items,
        "source": "daily_screening_pipeline",
        "mode": "added_date_cumulative",
        "generated_at": target_date.strftime("%Y-%m-%d"),
        "history_available": len(historical),
        "quote_available": quotes is not None and not quotes.empty,
    }


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
    runtime_config = get_effective_config()["config"]
    logger.info("=" * 50)
    logger.info(f"New France 筛选流水线启动 — {target_date}")
    logger.info("=" * 50)

    # ---- Layer 1: 数据采集 ----
    logger.info("[Layer 1] 数据采集...")
    collector = DataCollectorAgent()

    # 获取指数涨幅
    index_snapshot, index_meta = _read_index_snapshot_with_cache()
    index_gain = index_snapshot.get("gain_pct") or 0.0
    logger.info(
        "  上证指数: %s (%+.2f%%, %s)",
        index_snapshot.get("value") if index_snapshot.get("value") is not None else "--",
        index_gain,
        index_snapshot.get("source") or "--",
    )

    # 获取当日涨停股池
    zt_pool, zt_snapshot_meta = _read_zt_pool_with_cache(target_date)
    zt_list = []  # 涨停股列表（用于邮件通知）
    zt_meta = (zt_pool.attrs.get("source_meta") if zt_pool is not None else None) or zt_snapshot_meta

    # 交叉验证：Tushare 涨停数据（如已配置 TOKEN）
    try:
        from ..agents.layer1_data_collector.sources.tushare_source import fetch_zt_pool as ts_zt, is_available
        if is_available() and zt_pool is not None and not zt_pool.empty:
            ts_pool = ts_zt(target_date)
            if ts_pool is not None and not ts_pool.empty:
                em_codes = set(zt_pool["代码"].tolist())
                ts_codes = set(ts_pool["代码"].tolist())
                overlap = em_codes & ts_codes
                em_only = em_codes - ts_codes
                ts_only = ts_codes - em_codes
                if em_only or ts_only:
                    logger.warning(f"  涨停池交叉验证差异: 重合{len(overlap)}只, "
                                   f"仅东方财富{len(em_only)}只, 仅Tushare{len(ts_only)}只")
                zt_meta["cross_check"] = {
                    "tushare_count": len(ts_codes),
                    "overlap": len(overlap),
                    "eastmoney_only": list(em_only)[:10],
                    "tushare_only": list(ts_only)[:10],
                }
    except Exception as e:
        logger.debug(f"  Tushare 交叉验证跳过: {e}")

    if zt_pool is not None and not zt_pool.empty:
        zt_codes = zt_pool["代码"].tolist()
        logger.info(f"  涨停股池: {len(zt_codes)} 只")
        # 拉取涨停股的实时行情（获取量比、PE等补充字段）
        zt_quote_map = {}
        try:
            zt_quotes, _quote_meta = _read_quotes_with_cache(zt_codes)
            if zt_quotes is not None and not zt_quotes.empty:
                for _, row in zt_quotes.iterrows():
                    zt_quote_map[row["代码"]] = row
        except Exception as e:
            logger.warning(f"  涨停股行情拉取失败(将使用基本信息): {e}")
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

    optional_sources = {"sources": {}}
    try:
        from .optional_source_health import get_optional_source_statuses, is_optional_source_enabled
        optional_sources = get_optional_source_statuses()
    except Exception as e:
        logger.warning(f"  旁路数据源状态读取失败: {e}")

    # 读取监控列表
    watchlist = _read_watchlist()
    if not watchlist:
        return {
            "total_scored": 0, "strong_buy": 0, "buy": 0, "watch": 0,
            "results": [], "index_gain": index_gain, "index_value": index_snapshot.get("value"),
            "index_snapshot": index_snapshot, "zt_list": zt_list,
            "core_source_meta": {
                "zt_pool": zt_snapshot_meta,
                "index_snapshot": index_meta,
                "quotes": {},
            },
            "optional_sources": optional_sources,
            "errors": ["监控列表为空，请先添加股票"],
        }

    logger.info(f"  监控列表: {len(watchlist)} 只")

    national_team_summary = {}
    try:
        if is_optional_source_enabled("national_team", "email"):
            from .national_team_service import get_email_summary
            national_team_summary = get_email_summary()
        else:
            logger.info("  国家队动向仍为旁路数据源，未接入本次邮件")
    except Exception as e:
        logger.warning(f"  旁路数据源摘要读取失败: {e}")

    # 拉取实时行情快照（供阻断校验使用）
    codes = [e["code"] for e in watchlist]
    quotes, quotes_meta = _read_quotes_with_cache(codes)
    if quotes is None:
        quotes = pd.DataFrame()
    core_source_meta = {
        "zt_pool": zt_snapshot_meta,
        "index_snapshot": index_meta,
        "quotes": quotes_meta,
    }

    # 拉取历史K线 + 事件采集（并行，无数据依赖）
    event_engine = EventEngine()
    historical, events, principal_capital_map = await asyncio.gather(
        collector.collect_historical_batch(codes),
        event_engine.collect_daily_events(target_date),
        _fetch_principal_capital_map(target_date),
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

    # 构建降级价格映射：当实时行情失败时，优先使用涨停池(今日实时)，其次历史K线
    price_fallback = {}  # code -> {"最新价": float, "source": str}

    def _safe_fallback_price(val) -> float:
        """清洗价格：过滤 NaN / None / 非正数"""
        try:
            v = float(val)
        except (TypeError, ValueError):
            return 0.0
        if v != v or v <= 0:  # NaN != NaN 为 True
            return 0.0
        return v

    # 第一步：涨停池(今日实时数据，最优)
    if zt_pool is not None and not zt_pool.empty:
        for _, zt in zt_pool.iterrows():
            code = zt["代码"]
            price = _safe_fallback_price(zt.get("最新价", 0))
            if price > 0:
                price_fallback[code] = {"最新价": price, "source": "zt_pool"}

    # 第二步：历史K线收盘价(兜底)
    for code, hist in historical.items():
        if code in price_fallback:
            continue
        if hist is not None and not hist.empty:
            close_col = "收盘" if "收盘" in hist.columns else ("close" if "close" in hist.columns else None)
            if close_col:
                try:
                    last_close = _safe_fallback_price(hist.iloc[-1][close_col])
                    if last_close > 0:
                        price_fallback[code] = {"最新价": last_close, "source": "historical"}
                except (TypeError, ValueError, IndexError):
                    pass

    fallback_used = 0

    tracking_days = int(runtime_config["strategy"].get("trackingDays", 30))

    # 计算每只股票在配置周期内的涨停频率
    from .watchlist_store import count_zt_30days

    candidates = []
    for e in watchlist:
        code = e["code"]
        q = quote_map.get(code)
        if q is None:
            # 降级：从历史K线或涨停池获取价格
            fb = price_fallback.get(code)
            if fb is None or fb["最新价"] <= 0:
                continue
            q = fb
            fallback_used += 1

        current_price = q["最新价"]
        if current_price is None or current_price <= 0:
            continue

        hist = historical.get(code)
        watch_date = e.get("added_date", e["zt_date"])
        drop_pct = latest_cumulative_drawdown(
            hist,
            watch_date,
            e["ref_price"],
            current_price=current_price,
            current_date=target_date,
        )
        if drop_pct is None:
            continue

        # 回撤必须是价格低于参考价（drop_pct < 0），价格上涨的跳过
        if drop_pct >= 0:
            continue

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

        # 从 watchlist 读取当日封板数据，不再硬编码为0
        seal_time_raw = e.get("seal_time", "0")
        try:
            seal_time = int(seal_time_raw)
        except (ValueError, TypeError):
            seal_time = 0
        break_count_raw = e.get("break_count", e.get("炸板次数", "0"))
        try:
            break_count = int(break_count_raw)
        except (ValueError, TypeError):
            break_count = 0
        zt_count_raw = e.get("zt_count", "0")
        try:
            zt_count_stored = int(zt_count_raw)
        except (ValueError, TypeError):
            zt_count_stored = 0

        # 配置周期涨停频率：优先使用存储值，否则动态计算
        zt_frequency = zt_count_stored if zt_count_stored > 0 else count_zt_30days(code, days=tracking_days)

        candidates.append({
            "code": code,
            "name": e["name"],
            "zt_date": e["zt_date"],
            "ref_price": e["ref_price"],
            "current_price": current_price,
            "drop_pct": round(drop_pct, 2),
            "main_inflow_ratio": principal_capital_map.get(code),
            "涨跌幅": q.get("涨跌幅"),
            "换手率": q.get("换手率"),
            "市盈率": q.get("市盈率"),
            "量比": q.get("量比"),
            "总市值": q.get("总市值"),
            "流通市值": q.get("流通市值"),
            "封板时间": seal_time,
            "炸板次数": break_count,
            "涨停频率": zt_frequency,
            "added_date": e.get("added_date", e["zt_date"]),
            "price_history": _build_price_history_series(
                hist,
                watch_date,
                e["ref_price"],
                current_price,
                target_date,
            ),
        })

    if fallback_used > 0:
        logger.warning(f"  实时行情缺失 {fallback_used} 只，已用历史K线/涨停池价格降级")
    logger.info(f"  回撤检测 + 快速筛选后: {len(candidates)} 只候选")

    # ---- Layer 2: 多因子评分 ----
    logger.info("[Layer 2] 多因子评分...")
    engine = SignalEngineAgent(factor_weights=get_factor_weights_decimal(runtime_config),
                               strategy_config=runtime_config["strategy"])
    scored = engine.evaluate(candidates, quotes, historical, index_gain, event_map)

    # ---- Layer 2.5: 数据审核 ----
    logger.info("[Audit] 数据审核...")
    from ..agents.layer4_audit import AuditAgent
    auditor = AuditAgent(strict_mode=True)
    audit_results = auditor.audit_batch(scored, historical)

    # 应用审核降级
    audit_map = {}
    for ar in audit_results["stocks"]:
        audit_map[ar["code"]] = ar
    for s in scored:
        ar = audit_map.get(s.code)
        if ar and ar["downgraded"]:
            old_rec = s.recommendation
            s.recommendation = ar["adjusted_rec"]
            # 降级后重新计算得分上限
            if old_rec == "STRONG_BUY" and s.recommendation == "BUY":
                s.adjusted_score = min(s.adjusted_score, 69.0)
            elif old_rec == "BUY" and s.recommendation == "WATCH":
                s.adjusted_score = min(s.adjusted_score, 54.0)
            logger.info(f"  降级: {s.name}({s.code}) {old_rec} → {s.recommendation}")

    recommended_codes = {
        s.code for s in scored
        if str(s.recommendation).upper() in {"STRONG_BUY", "BUY", "WATCH"}
    }
    watchlist_drawdown_summary = _build_watchlist_drawdown_summary(
        watchlist,
        quotes,
        historical,
        target_date,
        tracking_days,
        recommended_codes,
    )

    # ---- Layer 3: 生成报告 ----
    logger.info("[Layer 3] 生成报告...")
    recom = RecommendationAgent()
    block_problems = _validate_core_sources_for_date(target_date, core_source_meta)
    execute_dry_run = dry_run or bool(block_problems)
    summary = await recom.execute(scored, target_date, index_gain,
                                  zt_list=zt_list, dry_run=execute_dry_run,
                                  audit_results=audit_results,
                                  zt_meta=zt_meta,
                                  index_snapshot=index_snapshot,
                                  national_team=national_team_summary)
    if block_problems:
        logger.error("核心行情源非目标交易日有效数据，阻断正式推荐外发: %s", block_problems)
        if not dry_run:
            send_data_integrity_alert(target_date, block_problems)

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
            "added_date": (s.extra or {}).get("added_date"),
            "ref_price": s.ref_price,
            "current_price": s.current_price,
            "drop_pct": s.drop_pct,
            "total_score": s.total_score,
            "event_impact": s.event_impact,
            "adjusted_score": s.adjusted_score,
            "recommendation": s.recommendation,
            "factors": factor_list,
            "price_history": s.extra.get("price_history", []) if getattr(s, "extra", None) else [],
            "audit": audit_map.get(s.code, {}),
            "evidence": _build_stock_evidence(s, historical, quotes_meta, zt_snapshot_meta, index_meta),
        })

    response = {
        "date": target_date.strftime("%Y-%m-%d"),
        "index_gain": index_gain,
        "index_value": index_snapshot.get("value"),
        "index_snapshot": index_snapshot,
        "zt_list": zt_list,
        "zt_meta": zt_meta,
        "core_source_meta": core_source_meta,
        "total_scored": summary["total_scored"],
        "strong_buy": summary["strong_buy"],
        "buy": summary["buy"],
        "watch": summary["watch"],
        "results": results,
        "report_md": summary.get("report_md"),
        "report_html": summary.get("report_html"),
        "optional_sources": optional_sources,
        "watchlist_drawdown_summary": watchlist_drawdown_summary,
        "errors": errors,
    }
    if block_problems:
        response["status"] = "blocked_stale_data"
        response["block_reason"] = block_problems
    return response
