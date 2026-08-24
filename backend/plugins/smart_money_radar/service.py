"""smart_money_radar Phase 1 编排服务。"""
import json
import logging
import math
import inspect
import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from backend.api.router_system import trading_session_status
from backend.plugins.principal_capital.service import (
    _is_excluded_market,
    _is_main_board,
    _is_st_name,
    _is_star_market,
    _stock_code,
    should_notify,
)

from .config import BEIJING_TZ, CONFIG, HISTORY_FILE, LATEST_FILE, NOTIFIED_DIR, REPLAY_DB_FILE, STATE_DIR
from .indicators import (
    active_buy_ratio,
    distance_from_high,
    fund_persistence_minutes,
    large_order_amounts,
    main_fund_metrics,
    order_book_strength,
    price_impact,
    price_returns,
    sell_pressure_decay,
    volume_ratio,
    vwap_deviation,
)
from .notifier import build_and_send
from .replay import RadarReplay
from .scoring import launch_score, smart_money_score
from .sources.tdx_source import TdxPool, poll_pool_once
from .store import RadarStore

logger = logging.getLogger(__name__)
_STORE = RadarStore()
_POOL_CACHE = {"expires_at": None, "items": [], "source_file": None}


def _state_file(today: date) -> Path:
    return STATE_DIR / f"smart_money_radar_state_{today.isoformat()}.json"


def load_stage_map(today: date) -> dict:
    path = _state_file(today)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("stages") or {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_stage_map(today: date, stages: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _state_file(today).write_text(
        json.dumps({"date": today.isoformat(), "stages": _json_safe(stages)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_history(rows: list) -> None:
    if not rows:
        return
    history = []
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            history = []
    if not isinstance(history, list):
        history = []
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(
        json.dumps(_json_safe(history + rows), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def transition_stage(previous: dict, metrics: dict, cfg: dict = None) -> dict:
    """按文档规定的顺序执行单步阶段迁移。"""
    cfg = cfg or CONFIG
    state = dict(previous or {})
    old_stage = state.get("stage") or "观察池"
    active = _float(metrics.get("active_buy_ratio"), 0) or 0
    net = _float(metrics.get("main_net_inflow"), 0) or 0
    change = _float(metrics.get("change_pct"), 0) or 0
    ratio = _float(metrics.get("volume_ratio"), 0) or 0
    persistence = _float(metrics.get("fund_persistence"), state.get("qualifying_minutes", 0)) or 0
    basic = active > float(cfg.get("active_buy_threshold", 0.55)) and net > float(cfg.get("main_net_inflow_threshold", 0))
    state["qualifying_rounds"] = int(state.get("qualifying_rounds", 0)) + 1 if basic else 0
    state["qualifying_minutes"] = persistence
    new_stage = old_stage

    failed = (
        net < 0
        or (_float(metrics.get("vwap_deviation"), 0) or 0) < 0
        or (_float(metrics.get("sell_surge"), 0) or 0) >= float(cfg.get("failure_sell_surge_ratio", 2.0))
    )
    if old_stage in {"启动前夕", "启动"} and failed:
        new_stage = "启动失败"
    elif old_stage == "启动前夕" and bool(metrics.get("breakout")) and ratio >= float(cfg.get("launch_volume_ratio", 1.5)) and active > float(cfg.get("active_buy_threshold", 0.55)) and (_float(metrics.get("buy_trend"), 0) or 0) >= float(cfg.get("buy_trend_threshold", 0.2)):
        new_stage = "启动"
    elif old_stage == "吸筹确认" and (
        (_float(metrics.get("smart_money_score"), 0) or 0) > float(cfg.get("prelaunch_smart_score", 80))
        and (_float(metrics.get("launch_score"), 0) or 0) > float(cfg.get("prelaunch_launch_score", 80))
        and (_float(metrics.get("decay_score"), 0) or 0) >= float(cfg.get("decay_threshold", 0.3))
        and (_float(metrics.get("distance_from_high"), 1) or 1) < float(cfg.get("prelaunch_distance_high", 0.01))
        and _float(metrics.get("price_impact")) is not None
        and _float(metrics.get("price_impact")) < float(cfg.get("prelaunch_price_impact_max", 1.0))
        and (_float(metrics.get("buy_trend"), 0) or 0) >= float(cfg.get("buy_trend_threshold", 0.2))
    ):
        new_stage = "启动前夕"
    elif old_stage == "潜伏" and active > float(cfg.get("absorption_active_buy", 0.60)) and net > float(cfg.get("absorption_net_inflow_threshold", 0)) and persistence >= float(cfg.get("absorption_minutes", 5)) and change < float(cfg.get("absorption_change_max", 0.02)) and (_float(metrics.get("vwap_deviation"), 0) or 0) > 0 and (_float(metrics.get("decay_score"), 0) or 0) >= 0:
        new_stage = "吸筹确认"
    elif old_stage == "观察池" and state["qualifying_rounds"] >= int(cfg.get("latent_rounds", 3)) and change < float(cfg.get("latent_change_max", 0.015)) and ratio > float(cfg.get("latent_volume_ratio", 1.2)):
        new_stage = "潜伏"
    state.update({"stage": new_stage, "previous_stage": old_stage, "changed": new_stage != old_stage})
    return state


def _json_safe(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _float(value, default=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _notified_file(today: date) -> Path:
    return NOTIFIED_DIR / f"smart_money_radar_notified_{today.isoformat()}.json"


def load_notified_map(today: date) -> dict:
    path = _notified_file(today)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("notified") or {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_notified_map(today: date, notified_map: dict) -> None:
    NOTIFIED_DIR.mkdir(parents=True, exist_ok=True)
    _notified_file(today).write_text(
        json.dumps({"date": today.isoformat(), "notified": notified_map}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def cleanup_old_notified(today: date, keep_days: int = 7) -> None:
    NOTIFIED_DIR.mkdir(parents=True, exist_ok=True)
    for path in NOTIFIED_DIR.glob("smart_money_radar_notified_*.json"):
        try:
            file_date = date.fromisoformat(path.stem.rsplit("_", 1)[-1])
        except ValueError:
            continue
        if (today - file_date).days > keep_days:
            path.unlink(missing_ok=True)


def _candidate_lists(payload: dict) -> list:
    items = []
    for key in CONFIG.get("pool_keys", []):
        value = payload.get(key)
        if isinstance(value, list):
            items.extend(value)
    # 兼容既有 principal_capital_latest.json：buy_candidates/sell_candidates
    # 是数量，实际可用的股票条目位于 buy_triggered/sell_triggered。
    if not items:
        for key in ("buy_triggered", "sell_triggered"):
            value = payload.get(key)
            if isinstance(value, list):
                items.extend(value)
    return items


def _valid_pool_item(item: dict) -> bool:
    code = _stock_code(item.get("code") or item.get("代码"))
    name = str(item.get("name") or item.get("名称") or "")
    if not code or not _is_main_board(code):
        return False
    if CONFIG.get("exclude_gem", True) and _is_excluded_market(code):
        return False
    if CONFIG.get("exclude_star", True) and _is_star_market(code):
        return False
    if _is_st_name(name):
        return False
    return True


def load_watch_pool(force: bool = False, now: Optional[datetime] = None) -> list:
    now = now or datetime.now(BEIJING_TZ)
    expires_at = _POOL_CACHE.get("expires_at")
    source_file = str(CONFIG["pool_source_file"])
    if not force and expires_at and now < expires_at and _POOL_CACHE.get("source_file") == source_file:
        return list(_POOL_CACHE["items"])
    path = Path(source_file)
    if not path.exists():
        _POOL_CACHE.update({
            "expires_at": now + timedelta(minutes=CONFIG["pool_refresh_min"]),
            "items": [],
            "source_file": source_file,
        })
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        payload = {}
    dedup = {}
    for raw in _candidate_lists(payload):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item["code"] = _stock_code(item.get("code") or item.get("代码"))
        item["name"] = item.get("name") or item.get("名称") or ""
        if _valid_pool_item(item):
            dedup[item["code"]] = item
    items = list(dedup.values())[: int(CONFIG["pool_max"])]
    _POOL_CACHE.update({
        "expires_at": now + timedelta(minutes=CONFIG["pool_refresh_min"]),
        "items": items,
        "source_file": source_file,
    })
    return items


def _update_notified_map(notified_map: dict, hits: list, now: datetime) -> dict:
    for item in hits:
        code = _stock_code(item.get("code"))
        notified_map.setdefault(code, []).append(now.isoformat())
    return notified_map


def _write_latest(payload: dict) -> None:
    LATEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    LATEST_FILE.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def read_latest() -> dict:
    if not LATEST_FILE.exists():
        return {"status": "not_run", "hits": []}
    try:
        return json.loads(LATEST_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "error", "reason": "latest 文件读取失败", "hits": []}


def _build_result(status: str, now: datetime, watch_pool: list, hits: list, errors: list = None, email=None) -> dict:
    return {
        "status": status,
        "now": now.isoformat(),
        "source": CONFIG.get("radar_source", "local"),
        "pool_count": len(watch_pool),
        "hit_count": len(hits),
        "hits": hits,
        "errors": errors or [],
        "email_sent": bool(email[0]) if email else False,
        "email_error": email[1] if email else None,
    }


def _current_day_bars(bars: list, now: datetime) -> list:
    """保留当前交易日分钟线；无日期字段时保留以兼容测试和降级源。"""
    result = []
    for bar in bars or []:
        raw = str((bar or {}).get("datetime") or "")
        if not raw or raw[:10] == now.date().isoformat():
            result.append(bar)
    return result


def _history_volume_by_minute(bars: list, now: datetime) -> dict:
    """将历史 1 分钟 bar 转为 minute -> 各交易日成交量。"""
    grouped = {}
    current_date = now.date().isoformat()
    for bar in bars or []:
        raw = str((bar or {}).get("datetime") or "")
        if len(raw) < 16 or raw[:10] == current_date:
            continue
        vol = _float((bar or {}).get("vol"))
        if vol is None or vol < 1:
            continue
        grouped.setdefault(raw[11:16], []).append(vol)
    return grouped


async def _phase2_data(pool, store, code: str, now: datetime, errors: list) -> tuple:
    """低频拉取分钟线和每日股本；单票失败只降级该票指标。"""
    bars_by_category = {}
    for category in (8, 0, 1):
        bars = store.get_cached_bars(code, category, now, CONFIG.get("bar_cache_ttl_s", 45))
        if bars is None:
            try:
                count = 800 if category == 8 else 10
                bars = await asyncio.wait_for(
                    asyncio.to_thread(pool.fetch_bars, 1 if code.startswith("6") else 0, code, category, count),
                    timeout=float(CONFIG.get("tdx_socket_timeout_s", 3)),
                )
                if bars:
                    store.cache_bars(code, category, bars, now)
            except asyncio.TimeoutError:
                errors.append(f"{code} bars[{category}]: TDX 读取超时")
                bars = []
            except Exception as exc:
                errors.append(f"{code} bars[{category}]: {exc}")
                bars = []
        bars_by_category[category] = bars

    finance = store.get_cached_finance(code, now)
    if finance is None:
        try:
            finance = await asyncio.wait_for(
                asyncio.to_thread(pool.fetch_finance, 1 if code.startswith("6") else 0, code),
                timeout=float(CONFIG.get("tdx_socket_timeout_s", 3)),
            )
            if finance:
                store.cache_finance(code, finance, now)
        except asyncio.TimeoutError:
            errors.append(f"{code} finance: TDX 读取超时")
            finance = {}
        except Exception as exc:
            errors.append(f"{code} finance: {exc}")
            finance = {}
    history_volume = _history_volume_by_minute(bars_by_category.get(8), now)
    bars_by_category[8] = _current_day_bars(bars_by_category.get(8), now)
    return bars_by_category, finance, history_volume


async def run_radar_once(
    now: Optional[datetime] = None,
    dry_run: bool = False,
    force: bool = False,
    store: Optional[RadarStore] = None,
    pool: Optional[TdxPool] = None,
) -> dict:
    now = now or datetime.now(BEIJING_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=BEIJING_TZ)
    store = store or _STORE
    store.ensure_date(now)
    session = trading_session_status(now)
    if not force and not session.get("is_trading_hours"):
        result = _build_result("skipped", now, [], [], errors=[session.get("market_status_text", "非交易时段")])
        _write_latest(result)
        return result

    watch_pool = load_watch_pool(now=now)
    if not watch_pool:
        result = _build_result("empty_pool", now, [], [])
        _write_latest(result)
        return result

    pool = pool or TdxPool()
    maybe_payloads = poll_pool_once(pool, watch_pool, CONFIG)
    payloads = await maybe_payloads if inspect.isawaitable(maybe_payloads) else maybe_payloads
    today = now.date()
    cleanup_old_notified(today, int(CONFIG["history_keep_days"]))
    notified_map = load_notified_map(today)
    stage_map = load_stage_map(today)
    hits = []
    replay_rows = []
    errors = []
    by_code = {item["code"]: item for item in watch_pool}
    for payload in payloads:
        if isinstance(payload, Exception):
            errors.append(str(payload))
            continue
        code = _stock_code(payload.get("code"))
        if payload.get("error"):
            errors.append(f"{code}: {payload.get('error')}")
            continue
        quote = dict(payload.get("quote") or {})
        metrics = order_book_strength(quote)
        active_ratio = active_buy_ratio(quote)
        quote["_strength_amt"] = metrics.get("strength_amt")
        store.add_quote(code, quote, now)
        store.update_transactions(code, payload.get("txs") or [], now)
        smooth = store.smooth_strength(code, int(CONFIG["strength_smooth_frames"]))
        pool_item = by_code.get(code) or payload.get("pool_item") or {}
        fund = main_fund_metrics(pool_item)
        large_orders = large_order_amounts(pool_item)
        store.add_fund_point(code, {"ts": now.isoformat(), **fund})
        strength_hit = smooth >= float(CONFIG["strength_threshold"])
        active_hit = active_ratio is not None and active_ratio >= float(CONFIG["active_buy_threshold"])
        ratio = _float(fund.get("main_inflow_ratio"), 0) or 0
        net = _float(fund.get("main_net_inflow"), 0) or 0
        fund_hit = ratio >= float(CONFIG["main_inflow_ratio_threshold"]) and net >= float(CONFIG["main_net_inflow_threshold"])
        bars_by_category, finance, history_volume = await _phase2_data(pool, store, code, now, errors)
        price = _float(quote.get("price"))
        last_close = _float(quote.get("last_close"))
        change_pct = _float(quote.get("change_pct"))
        if change_pct is None and price is not None and last_close:
            change_pct = (price - last_close) / last_close
        raw_price_impact = price_impact(
            change_pct,
            fund.get("main_net_inflow"),
            price,
            finance.get("liutongguben"),
        )
        phase2 = {
            **price_returns(bars_by_category),
            "vwap_deviation": vwap_deviation(price, bars_by_category.get(8)),
            "distance_from_high": distance_from_high(price, bars_by_category.get(8)),
            "price_impact": max(0.0, raw_price_impact) if raw_price_impact is not None else None,
            "volume_ratio": volume_ratio(
                bars_by_category.get(8), payload.get("history_volume") or history_volume
            ),
        }
        decay = sell_pressure_decay(
            list(store.quotes.get(code, [])), store.minute_buckets.get(code, {}), now,
            int(CONFIG.get("decay_window_s", 90)), int(CONFIG.get("decay_minutes", 3)),
        )
        quote_rows = list(store.quotes.get(code, []))
        strengths = [row.get("quote", {}).get("_strength_amt") for row in quote_rows]
        strengths = [float(value) for value in strengths if isinstance(value, (int, float))]
        buy_trend = min(1.0, max(0.0, ((strengths[-1] - strengths[0]) / 20))) if len(strengths) >= 2 else 0.0
        one_minute_bars = bars_by_category.get(8) or []
        prior_bars = one_minute_bars[-21:-1]
        breakout = bool(price is not None and prior_bars and price > max((_float(bar.get("high"), 0) or 0) for bar in prior_bars))
        phase3 = {
            **decay,
            "fund_persistence": fund_persistence_minutes(list(store.fund_series.get(code, []))),
            "buy_trend": buy_trend,
            "breakout": breakout,
            "change_pct": change_pct,
        }
        combined = {**large_orders, **fund, **phase2, **phase3, "active_buy_ratio": active_ratio, "strength_smooth": smooth}
        combined["smart_money_score"] = smart_money_score(combined, CONFIG.get("smart_money_weights"))
        combined["launch_score"] = launch_score(combined, CONFIG.get("launch_weights"))
        previous_state = stage_map.get(code) or {"stage": "观察池"}
        next_state = transition_stage(previous_state, combined)
        stage_map[code] = next_state
        combined["stage"] = next_state["stage"]
        combined["stage_changed"] = next_state.get("changed", False)
        replay_rows.append({
            "time": now.isoformat(), "code": code, "stage": next_state["stage"],
            "smart_money_score": combined["smart_money_score"],
            "launch_score": combined["launch_score"], "metrics": combined,
        })
        phase3_signal = next_state.get("changed") and next_state["stage"] in {"吸筹确认", "启动前夕", "启动", "启动失败"}
        if phase3_signal and should_notify(
            code, "radar", now, notified_map, int(CONFIG["alert_cooldown_minutes"])
        ):
            hits.append({
                "code": code,
                "name": payload.get("name") or pool_item.get("name") or "",
                "stage": next_state["stage"],
                "price": price,
                "strength_amt": metrics.get("strength_amt"),
                "strength_smooth": smooth,
                "active_buy_ratio": active_ratio,
                **large_orders,
                **fund,
                **phase2,
                **phase3,
                "smart_money_score": combined["smart_money_score"],
                "launch_score": combined["launch_score"],
            })

    email = (False, None)
    if hits and not dry_run:
        email = build_and_send(hits, CONFIG.get("radar_source", "local"), now)
    if not dry_run and email[0]:
        save_notified_map(today, _update_notified_map(notified_map, hits, now))
    if not dry_run:
        save_stage_map(today, stage_map)
    if not dry_run and hits:
        append_history([
            {"time": now.isoformat(), "code": item.get("code"), "stage": item.get("stage"),
             "smart_money_score": item.get("smart_money_score"), "launch_score": item.get("launch_score"),
             "metrics": item}
            for item in hits
        ])
    if not dry_run and CONFIG.get("enable_sqlite_dump") and replay_rows:
        try:
            await asyncio.to_thread(RadarReplay(REPLAY_DB_FILE).dump, replay_rows)
        except Exception as exc:
            errors.append(f"SQLite 回放写入失败: {exc}")
    result = _build_result("completed", now, watch_pool, hits, errors=errors, email=email)
    _write_latest(result)
    return result
