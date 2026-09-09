"""22 智能选股聚合中枢编排服务（路径B：本地聚合快照 + web 远端兜底读）。"""
import asyncio
import json
import logging
from datetime import date, datetime

from backend.plugins.common import (
    BEIJING_TZ, db_append, db_delete, db_query, float_or, get_kline_cached, json_safe,
    read_snapshot, read_snapshot_resilient, snapshot_mem_get, snapshot_mem_set, write_snapshot,
)
from backend.plugins.multi_hit_notifier import push_multi_hit
from backend.services.trading_calendar import is_trading_day, prev_trading_day, trading_days_between_dates

from .config import (
    BADGE_SOURCES, CHARTS_SNAPSHOT_NAME, CONFIG, REPORT_DIR, SNAPSHOT_MAP, SNAPSHOT_NAME, STRATEGY_KEYS,
)
from .indicators import (
    apply_gates, build_chart_series, compute_hub_score, explain_chip, explain_pattern, explain_tech,
    explain_trend, extract_rows, fill_strategy_pct, filter_items, normalize_weights, percentiles,
    trading_days_after, union_table, zcode,
)

logger = logging.getLogger(__name__)

RESONANCE_COLS = [
    ("name", "名称"), ("price", "价格"), ("hit_strategies", "命中策略数"),
    ("hub_score", "综合分"), ("hub_score_pct", "百分位"),
]
EXPLAINERS = {"tech": explain_tech, "trend": explain_trend, "pattern": explain_pattern, "chip": explain_chip}


def _to_date(value) -> date:
    """date/datetime/ISO 字符串统一为 date（沿用既有插件口径）。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return datetime.now(BEIJING_TZ).date()


# ==== 快照读取 ====

def _latest_cached() -> dict:
    """主快照内存缓存（<1ms）；冷启动本地文件优先、data-snapshots raw 兜底。"""
    cached = snapshot_mem_get(SNAPSHOT_NAME)
    if cached is not None:
        return cached
    payload = read_snapshot_resilient(SNAPSHOT_NAME, ttl_seconds=int(CONFIG["remote_ttl_seconds"]))
    snapshot_mem_set(SNAPSHOT_NAME, payload)
    return payload


def _charts_cached() -> dict:
    """图表快照内存缓存（单文件一次性加载，逐 code 读取 <1ms）。"""
    cached = snapshot_mem_get(CHARTS_SNAPSHOT_NAME)
    if cached is not None:
        return cached
    payload = read_snapshot_resilient(CHARTS_SNAPSHOT_NAME, ttl_seconds=int(CONFIG["remote_ttl_seconds"]))
    snapshot_mem_set(CHARTS_SNAPSHOT_NAME, payload)
    return payload


def load_strategy_rows() -> tuple:
    """读四份策略快照 → 每策略去重行 + pct 兜底。返回 (snaps, rows)。"""
    snaps, rows = {}, {}
    for k in STRATEGY_KEYS:
        snap = read_snapshot_resilient(SNAPSHOT_MAP[k], ttl_seconds=int(CONFIG["remote_ttl_seconds"]))
        snaps[k] = snap
        r = extract_rows(snap)
        fill_strategy_pct(r, k)
        rows[k] = r
    return snaps, rows


def _strategy_status(snapshot: dict, rows: list) -> dict:
    """可用性/降级判定：completed+有行=可用；有 items 缺 pools=旧格式降级。"""
    status = (snapshot or {}).get("status")
    if status == "completed" and rows:
        has_pools = any(k.endswith("_pool") for k in (snapshot or {}))
        return {
            "available": True, "degraded": not has_pools, "count": len(rows),
            "snapshot_date": (snapshot or {}).get("date"),
        }
    return {
        "available": False, "degraded": False, "count": 0,
        "reason": str((snapshot or {}).get("reason") or status or "snapshot_unavailable"), "snapshot_date": None,
    }


def load_zt_codes() -> set:
    """当日涨停代码集合（data_backend/zt_pool.json 本地优先，raw 兜底）；不可用返回 None。"""
    from backend.services.data_backend.snapshots import _fetch_remote_snapshot_json, _read_local_snapshot

    snap = _read_local_snapshot("zt_pool") or _fetch_remote_snapshot_json("zt_pool")
    if not isinstance(snap, dict):
        return None
    codes = snap.get("codes") or []
    if codes:
        return {zcode(c) for c in codes}
    records = snap.get("records") or []
    if records:
        return {zcode(r.get("代码") or r.get("code")) for r in records if (r.get("代码") or r.get("code"))}
    return None


def load_badge_sources() -> dict:
    """交叉 badge 源（缺失即空 dict，绝不编造）。"""
    out = {"fund_flow": {}, "tier_state": {}, "radar": {}}
    try:
        snap = read_snapshot_resilient(BADGE_SOURCES["fund_flow"], ttl_seconds=int(CONFIG["remote_ttl_seconds"]))
        for key in ("buy_candidates", "sell_candidates"):
            for it in snap.get(key) or []:
                if not isinstance(it, dict) or not it.get("code"):
                    continue
                out["fund_flow"].setdefault(zcode(it["code"]), {
                    "main_net_inflow": float_or(it.get("main_net_inflow")),
                    "main_inflow_ratio": float_or(it.get("main_inflow_ratio")),
                    "source": "principal_capital",
                })
    except Exception as exc:  # noqa: BLE001
        logger.debug("资金流 badge 源不可用: %s", exc)
    try:
        snap = read_snapshot_resilient(BADGE_SOURCES["tier_state"], ttl_seconds=int(CONFIG["remote_ttl_seconds"]))
        for it in snap.get("items") or []:
            if isinstance(it, dict) and it.get("code") and it.get("state"):
                out["tier_state"][zcode(it["code"])] = it.get("state")
    except Exception as exc:  # noqa: BLE001
        logger.debug("分层资金流 badge 源不可用: %s", exc)
    try:
        snap = read_snapshot_resilient(BADGE_SOURCES["radar"], ttl_seconds=int(CONFIG["remote_ttl_seconds"]))
        for it in snap.get("hits") or []:
            if not isinstance(it, dict) or not it.get("code"):
                continue
            strength = float_or(it.get("strength_smooth") if it.get("strength_smooth") is not None
                                else it.get("strength"))
            if strength is None:
                continue
            out["radar"][zcode(it["code"])] = {"strength": strength, "source": "smart_money_radar"}
    except Exception as exc:  # noqa: BLE001
        logger.debug("雷达 badge 源不可用: %s", exc)
    return out


def attach_badges(items: list) -> tuple:
    """给 items 挂只读 badge（缺失省略，不参与分数）。返回 (items, badges_missing)。"""
    sources = load_badge_sources()
    missing = 0
    for it in items:
        badges = {}
        for key in ("fund_flow", "tier_state", "radar"):
            v = sources[key].get(it["code"])
            if v:
                badges[key] = v
        if badges:
            it["badges"] = badges
        else:
            missing += 1
    return items, missing


# ==== 图表预计算（cron 时 K 线缓存仍热，不额外拉网络） ====

def _hist_to_records(hist) -> list:
    """历史 K 线 DataFrame → [{date,open,close,high,low,vol}]（JSON 安全）。"""
    if hist is None or getattr(hist, "empty", True):
        return []

    def col(*names):
        for n in names:
            if n in hist.columns:
                return hist[n].tolist()
        return None

    dates = col("日期", "date")
    opens = col("开盘", "open")
    closes = col("收盘", "close")
    highs = col("最高", "high")
    lows = col("最低", "low")
    vols = col("成交量", "vol", "volume")
    records = []
    for i in range(len(hist)):
        records.append({
            "date": str(dates[i])[:10] if dates else None,
            "open": float_or(opens[i]) if opens else None,
            "close": float_or(closes[i]) if closes else None,
            "high": float_or(highs[i]) if highs else None,
            "low": float_or(lows[i]) if lows else None,
            "vol": float_or(vols[i]) if vols else None,
        })
    return json_safe(records)


def build_chart_payload(code: str, days: int, kline_fetcher=None) -> dict:
    """个股图表：日线 OHLC + MA/MACD/KDJ/RSI/BOLL 全序列（复用当日 K 线缓存）。"""
    hist = get_kline_cached(zcode(code), int(days), kline_fetcher)
    records = _hist_to_records(hist)[-int(days):]  # 缓存可能含更长窗口，按 days 截尾
    if not records:
        return {"records": [], "series": {}}
    return {"records": records, "series": build_chart_series(records)}


async def precompute_charts(items: list, cfg: dict, kline_fetcher=None) -> tuple:
    """对榜单前 N 只预算图表。返回 (codes, charts)。失败单票跳过，不阻断。"""
    codes = [it["code"] for it in items[: int(cfg["chart_precompute_n"])]]
    charts = {}
    for code in codes:
        try:
            payload = await asyncio.to_thread(build_chart_payload, code, int(cfg["chart_days"]), kline_fetcher)
            if payload.get("records"):
                charts[code] = payload
        except Exception as exc:  # noqa: BLE001
            logger.debug("图表预计算失败 %s: %s", code, exc)
    return codes, charts


# ==== 信号质量追踪（无前视回填） ====

def refresh_perf(items: list, target: date, cfg: dict, kline_fetcher=None) -> dict:
    """每日入库今日 top N 信号 + 回填 T+1/T+3/T+5 收益（只填 signal_date+k ≤ 计算日）。"""
    cfg = cfg or CONFIG
    top = sorted(items, key=lambda r: (-(float_or(r.get("hub_score")) or 0), r["code"]))[: int(cfg["perf_track_top_n"])]
    for it in top:
        existing = db_query(
            "SELECT id FROM picker_perf_daily WHERE signal_date = :d AND code = :c",
            {"d": target.isoformat(), "c": it["code"]},
        )
        if existing.empty:
            db_append("picker_perf_daily", [{
                "signal_date": target.isoformat(), "code": it["code"],
                "strategies": ",".join(k for k in STRATEGY_KEYS if it["hit_flags"].get(k)),
                "hub_score": it.get("hub_score"), "close_entry": it.get("price"),
            }])
    start = prev_trading_day(target, max(0, int(cfg["perf_lookback_days"]) - 1)) or target
    scope_dates = trading_days_between_dates(start, target)
    if not scope_dates:
        return {"tracked": len(top), "filled": 0, "missing": 0}
    df = db_query("SELECT * FROM picker_perf_daily WHERE signal_date >= :s", {"s": scope_dates[0].isoformat()})
    filled = missing = 0
    for _, row in df.iterrows():
        signal_date = _to_date(row.get("signal_date"))
        windows = [int(w) for w in cfg["perf_windows"]]
        updates = {}
        try:
            hist = get_kline_cached(zcode(row.get("code")), 130, kline_fetcher)
            bar_dates = []
            closes_by_date = {}
            date_col = next((c for c in hist.columns if c in ("日期", "date")), None)
            close_col = next((c for c in hist.columns if c in ("收盘", "close")), None)
            if date_col and close_col:
                bar_dates = [str(d)[:10] for d in hist[date_col].tolist()]
                closes_by_date = dict(zip(bar_dates, [float_or(v) for v in hist[close_col].tolist()]))
        except Exception as exc:  # noqa: BLE001
            logger.debug("绩效回填 K 线拉取失败 %s: %s", row.get("code"), exc)
            continue
        entry = closes_by_date.get(signal_date.isoformat()) or float_or(row.get("close_entry"))
        for k in windows:
            if int(row.get(f"t{k}_filled") or 0):
                continue
            # DIFF-6：交易日序列直接从 bar 日期推导，不依赖外部日历
            nd_str = trading_days_after(bar_dates, signal_date.isoformat(), k)
            if nd_str is None:  # bars 不足 k 条 / 锚点缺失 → 停牌类缺口
                updates["data_missing"] = 1
                missing += 1
                continue
            nd = _to_date(nd_str)
            if nd > target:  # 未到期/未来数据不可见（无前视）
                continue
            c = closes_by_date.get(nd_str)
            if c is None:
                updates["data_missing"] = 1
                missing += 1
                continue
            updates[f"t{k}_close"] = c
            updates[f"t{k}_ret"] = round(c / entry - 1, 4) if entry else None
            updates[f"t{k}_filled"] = 1
            filled += 1
        if updates:
            merged = {str(kk): vv for kk, vv in row.to_dict().items()}
            merged.update(updates)
            merged["updated_at"] = datetime.now(BEIJING_TZ).isoformat()
            db_delete("picker_perf_daily", {"signal_date": signal_date.isoformat(), "code": zcode(row.get("code"))})
            db_append("picker_perf_daily", [merged])
    return {"tracked": len(top), "filled": filled, "missing": missing}


def read_perf(days: int, window: str, cfg: dict = None) -> dict:
    """信号质量卡：各策略近 N 交易日 T+1/3/5 收益汇总（样本<阈值不出结论）。"""
    cfg = cfg or CONFIG
    window = str(window).lower()
    k = {"t1": 1, "t3": 3, "t5": 5}.get(window, 1)
    ret_key, filled_key = f"t{k}_ret", f"t{k}_filled"
    today = datetime.now(BEIJING_TZ).date()
    start = prev_trading_day(today, max(0, int(days) - 1)) or today
    df = db_query("SELECT * FROM picker_perf_daily WHERE signal_date >= :s ORDER BY signal_date DESC", {"s": start.isoformat()})
    records = json_safe(df.to_dict("records")) if not df.empty else []
    sample_min = int(cfg["perf_sample_min"])
    summary, sample_counts = {}, {}
    for strat in list(STRATEGY_KEYS) + ["resonance"]:
        sub = []
        for r in records:
            strat_list = [s for s in str(r.get("strategies") or "").split(",") if s]
            hit = (len(strat_list) >= 2) if strat == "resonance" else (strat in strat_list)
            if hit:
                sub.append(r)
        rets = [r[ret_key] for r in sub if r.get(filled_key) == 1 and r.get(ret_key) is not None]
        n = len(rets)
        insufficient = n < sample_min
        summary[strat] = {
            "n": n, "insufficient": insufficient,
            "avg_ret": None if (insufficient or not rets) else round(sum(rets) / len(rets), 4),
            "win_rate": None if (insufficient or not rets) else round(sum(1 for x in rets if x > 0) / len(rets), 4),
        }
        sample_counts[strat] = n
    return {
        "status": "ok", "window": window, "days": days, "sample_min": sample_min,
        "sample_counts": sample_counts, "summary": summary,
        "insufficient": [s for s, m in summary.items() if m["insufficient"]],
        "records": records[:200],
    }


# ==== 主流程 ====

async def run_smart_picker_hub_once(target_date=None, force: bool = False, notifier=None,
                                    kline_fetcher=None) -> dict:
    """盘后聚合：读四策略快照 → 去重合并 → 门控 → 统一分 → badges → 图表 → 快照/入库/绩效/邮件。"""
    now = datetime.now(BEIJING_TZ)
    target = _to_date(target_date) if target_date is not None else now.date()
    # DIFF-4：并发写保护（db_delete+db_append 非原子；cron 与手动并发时第二个进程跳过）
    lock_path = REPORT_DIR / ".hub.lock"
    if lock_path.exists():
        logger.warning("检测到 %s（另一进程聚合中），本轮跳过", lock_path.name)
        return {"status": "skipped", "reason": "hub_lock_held", "date": target.isoformat(), "items": []}
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(now.isoformat(), encoding="utf-8")
    try:
        return await _hub_run_locked(now, target, force, notifier, kline_fetcher)
    finally:
        try:
            lock_path.unlink()
        except OSError:  # noqa: BLE001
            pass


async def _hub_run_locked(now: datetime, target: date, force: bool, notifier=None, kline_fetcher=None) -> dict:
    """聚合主逻辑（调用方须先取得 .hub.lock）。"""
    if not force and not is_trading_day(target):
        payload = {"status": "skipped", "reason": "非交易日", "date": target.isoformat(), "items": []}
        write_snapshot(SNAPSHOT_NAME, payload)
        return payload
    cfg = CONFIG
    snaps, rows = load_strategy_rows()
    statuses = {k: _strategy_status(snaps[k], rows[k]) for k in STRATEGY_KEYS}
    available = {k: bool(s["available"]) for k, s in statuses.items()}
    if not any(available.values()):
        payload = {"status": "no_data", "reason": "all_strategies_unavailable", "date": target.isoformat(),
                   "items": [], "strategies": statuses}
        write_snapshot(SNAPSHOT_NAME, payload)
        return payload
    weights = normalize_weights(cfg["weights"], available)
    items = union_table(rows)
    for it in items:
        it["resonance"] = 1 if it["hit_strategies"] >= int(cfg["resonance_min_hit"]) else 0
        it["hub_score"] = compute_hub_score(it["pct"], weights)
    zt_codes = load_zt_codes() if cfg["exclude_limit_up"] else None
    items, zt_applied, zt_reason = apply_gates(items, zt_codes, cfg)
    pcts = percentiles([it["hub_score"] for it in items])
    for it, p in zip(items, pcts):
        it["hub_score_pct"] = p
    for it in items:
        for k in list(it.get("hits") or {}):
            hit = dict(it["hits"][k])
            hit["explain"] = EXPLAINERS[k](hit)
            it["hits"][k] = hit
    items, badges_missing = attach_badges(items)
    items.sort(key=lambda r: (-(float_or(r.get("hub_score")) or 0), -(int(r.get("hit_strategies") or 0)), r["code"]))
    items = items[: int(cfg["top_n"])]
    chart_codes, charts = await precompute_charts(items, cfg, kline_fetcher)
    perf = refresh_perf(items, target, cfg, kline_fetcher)
    snapshot_dates = [s.get("snapshot_date") for s in statuses.values() if s.get("snapshot_date")]
    data_age = (target - max(_to_date(d) for d in snapshot_dates)).days if snapshot_dates else None
    prev_snapshot = read_snapshot(SNAPSHOT_NAME) or {}
    prev_res = {zcode(i.get("code")) for i in prev_snapshot.get("items") or [] if i.get("resonance")}
    new_res = [it for it in items if it["resonance"] and it["code"] not in prev_res]
    email_sent, _, email_error = push_multi_hit(
        "hub", "智能选股", "共振池", new_res, now,
        int(cfg["notify_cooldown_minutes"]), RESONANCE_COLS, notifier)
    degraded = [k for k, s in statuses.items() if not s["available"]]
    signal_counts = {
        "total": len(items),
        "resonance": sum(1 for it in items if it["resonance"]),
        "by_strategy": {k: sum(1 for it in items if it["hit_flags"].get(k)) for k in STRATEGY_KEYS},
        "badges_missing": badges_missing,
    }
    payload = {
        "status": "degraded" if degraded else "completed",
        "run_at": now.isoformat(),
        "date": target.isoformat(),
        "data_age_days": data_age,
        "source": "local",
        "weights": weights,
        "strategies": statuses,
        "zt_gate_applied": bool(zt_applied),
        "zt_gate_reason": zt_reason,
        "signal_counts": signal_counts,
        "count": len(items),
        "items": items,
        "charts_precomputed": {"count": len(charts), "n": int(cfg["chart_precompute_n"]), "codes": chart_codes[: int(cfg["chart_precompute_n"])]},
        "perf": perf,
        "email_sent": bool(email_sent), "email_error": email_error,
        "disclaimer": "信号仅为辅助参考，不构成投资建议",
    }
    write_snapshot(SNAPSHOT_NAME, payload)
    write_snapshot(CHARTS_SNAPSHOT_NAME, {
        "status": "completed", "run_at": now.isoformat(), "date": target.isoformat(),
        "count": len(charts), "charts": charts,
    })
    if items:
        db_delete("smart_picker_hits", {"date": target.isoformat()})
        db_append("smart_picker_hits", [
            {
                "date": target.isoformat(),
                **{kk: it.get(kk) for kk in ("code", "name", "price", "change_pct", "total_amount",
                                             "hub_score", "hub_score_pct", "hit_strategies", "resonance")},
                **{f"{k}_hit": it["hit_flags"].get(k, 0) for k in STRATEGY_KEYS},
                "badges_json": json.dumps(it.get("badges") or {}, ensure_ascii=False),
                "raw_json": json.dumps(it, ensure_ascii=False, default=str),
            }
            for it in items
        ])
    return payload


# ==== 查询（router 用） ====

def query_latest(q: str = "", pool: str = "all", min_hit: int = 1, market: str = "main",
                 sort: str = "hub_score", order: str = "desc", limit: int = 50, offset: int = 0) -> dict:
    """主列表查询：快照内存 → 服务端过滤/排序/分页（<1ms）。"""
    payload = _latest_cached()
    if payload.get("status") != "completed":
        return payload
    filtered = filter_items(payload.get("items") or [], q, pool, min_hit, market, sort, order, limit, offset)
    result = dict(payload)
    result["source"] = payload.get("_source") or payload.get("source") or "local"
    result["total"] = len(filtered["all"])
    result["returned"] = len(filtered["page"])
    result["items"] = filtered["page"]
    return result


def query_history(date_str: str, q: str = "", pool: str = "all", min_hit: int = 1, market: str = "main",
                    sort: str = "hub_score", order: str = "desc", limit: int = 50, offset: int = 0) -> dict:
    """历史日期查询：本地 SQLite smart_picker_hits（raw_json 反序列化）；云端无历史。"""
    df = db_query("SELECT raw_json FROM smart_picker_hits WHERE date = :d", {"d": str(date_str)[:10]})
    if df.empty:
        served_remote = bool((_latest_cached() or {}).get("_source") == "snapshot")
        return {"status": "local_only_unavailable" if served_remote else "no_data",
                "date": str(date_str)[:10], "reason": "no_history", "items": []}
    items = []
    for raw in df["raw_json"].tolist():
        try:
            items.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue
    filtered = filter_items(items, q, pool, min_hit, market, sort, order, limit, offset)
    return {"status": "ok", "date": str(date_str)[:10],
            "total": len(filtered["all"]), "returned": len(filtered["page"]), "items": filtered["page"]}


def query_code(code: str, date_str: str = None) -> dict:
    """个股当日命中（快照内存 filter，不走 SQLite）；带 date 走本地历史。"""
    code = zcode(code)
    if date_str is not None:
        df = db_query("SELECT * FROM smart_picker_hits WHERE code = :c AND date = :d",
                      {"c": code, "d": str(date_str)[:10]})
        if not df.empty:
            return {"status": "ok", "code": code, "date": str(date_str)[:10],
                    "item": json_safe(json.loads(df.iloc[0]["raw_json"]))}
        served_remote = bool((_latest_cached() or {}).get("_source") == "snapshot")
        return {"status": "local_only_unavailable" if served_remote else "ok", "code": code,
                "date": str(date_str)[:10], "item": None, "note": "no_history"}
    payload = _latest_cached()
    for it in payload.get("items") or []:
        if zcode(it.get("code")) == code:
            return {"status": "ok", "code": code, "item": it}
    return {"status": "ok", "code": code, "item": None, "note": "no_hit_today"}


def query_meta() -> dict:
    """元信息：权重/策略可用性/门控/图表/绩效配置。"""
    payload = _latest_cached()
    cfg = CONFIG
    return {
        "version": "22.1",
        "weights": payload.get("weights") or cfg["weights"],
        "strategies_availability": {k: bool((payload.get("strategies") or {}).get(k, {}).get("available"))
                                    for k in STRATEGY_KEYS},
        "data_age_days": payload.get("data_age_days"),
        "gate": {"exclude_limit_up": bool(cfg["exclude_limit_up"]),
                 "zt_gate_applied": bool(payload.get("zt_gate_applied")),
                 "zt_source": "data_backend/zt_pool.json"},
        "chart": {"precompute_n": int(cfg["chart_precompute_n"]), "days": int(cfg["chart_days"])},
        "perf": {"windows": list(cfg["perf_windows"]), "lookback_days": int(cfg["perf_lookback_days"]),
                 "sample_min": int(cfg["perf_sample_min"])},
        "sort_whitelist": ["hub_score", "hit_strategies", "price", "change_pct", "total_amount", "code"],
    }
