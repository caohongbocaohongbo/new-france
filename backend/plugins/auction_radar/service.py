"""28 低位竞价雷达编排：采集(poll)与最终确认(finalize)分离 + 真实性门禁 + SQL UPDATE。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from datetime import datetime

from sqlalchemy import text as sql_text

from backend.db.database import engine
from backend.plugins.common import db_append, db_delete, db_query, json_safe
from . import scoring as _scoring
from .config import BEIJING_TZ, CONFIG, LATEST_FILE, PRELIMINARY_FILE
from .collector import TencentAuctionSource, poll_pool_once
from .models import AuctionQuote
from .pool import freeze_pool, prepare_low_pool
from .quality import validate_quote
from .snapshot import build_snapshot_payload, new_snapshot_version, publish_snapshot
from .store import AuctionStore

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(BEIJING_TZ)


def _float(value, default=None):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _config_hash():
    return hashlib.sha256(json.dumps(CONFIG, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def _code_commit():
    try:
        import subprocess
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       cwd="/Users/fangcang/new-france").decode().strip()
    except Exception:
        return "unknown"


def read_latest():
    if not LATEST_FILE.exists():
        return {"status": "not_run", "items": []}
    try:
        return json.loads(LATEST_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "error", "items": []}


_TERMINAL_STATES = {"PUBLISHED", "UNCONFIRMED", "FAILED", "SKIPPED_NO_POOL", "DEGRADED_STALE_POOL"}


def claim_run_once(trade_date, strategy_version, revision=1):
    """DB 唯一键抢占；非终态允许 resume（异常重试不重复 INSERT）。"""
    existing = db_query(
        "SELECT id, state FROM auction_runs WHERE trade_date=:d AND strategy_version=:s AND revision=:r",
        {"d": trade_date, "s": strategy_version, "r": revision},
    )
    if not existing.empty:
        row = existing.iloc[0]
        state = row["state"]
        run_id = int(row["id"])
        if state in _TERMINAL_STATES:
            return {"claimed": False, "run_id": run_id, "state": state, "terminal": True}
        return {"claimed": True, "run_id": run_id, "state": state, "resume": True}
    db_append("auction_runs", [{
        "trade_date": trade_date, "strategy_version": strategy_version, "revision": revision,
        "phase": "INIT", "state": "INIT", "started_at": _now().isoformat(),
    }])
    created = db_query(
        "SELECT id FROM auction_runs WHERE trade_date=:d AND strategy_version=:s AND revision=:r",
        {"d": trade_date, "s": strategy_version, "r": revision},
    )
    return {"claimed": True, "run_id": int(created.iloc[0]["id"]), "state": "INIT", "resume": False}


def update_run(run_id, **fields):
    """SQL UPDATE（非追加插入）；返回 rowcount。"""
    if not fields:
        return 0
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    params = dict(fields)
    params["id"] = int(run_id)
    try:
        with engine.begin() as conn:
            return conn.execute(sql_text(f"UPDATE auction_runs SET {sets} WHERE id = :id"), params).rowcount
    except Exception as exc:  # noqa: BLE001
        logger.warning("update_run 失败 run_id=%s fields=%s err=%s", run_id, list(fields), exc)
        return 0


def update_run_checked(run_id, **fields):
    """UPDATE 并检查 rowcount；失败抛异常阻止误认为已落库。"""
    n = update_run(run_id, **fields)
    if n != 1:
        raise RuntimeError(f"update_run rowcount={n} run_id={run_id} fields={list(fields)}")
    return n


def _gap_factor(q):
    from . import factors as _factors
    if q.indicative_price_yuan and q.prev_close_yuan:
        return _factors.auction_gap(q.indicative_price_yuan, q.prev_close_yuan)
    return None


def _score_item(pool_item, factors, branch):
    if branch == "B-":
        weights = {"gap": 1.0}
    elif branch in ("A", "B"):
        weights = {"gap": 0.5, "orderbook_imbalance": 0.3, "limit_up_seal": 0.2}
    else:
        weights = {}
    auction, coverage = _scoring.weighted_auction_score(
        factors, weights, min_coverage=CONFIG["min_factor_coverage"]) if weights else (None, 0.0)
    low = _float(pool_item.get("low_score"))
    total = None
    if low is not None or auction is not None:
        total = _scoring.total_score(low, auction, 0.0, CONFIG.get("w_low", 0.40), CONFIG.get("w_auction", 0.60))
    return auction, coverage, total


def _quote_factors(q, frames):
    from . import factors as _factors
    gap = _gap_factor(q)
    imbalance = None
    if q.orderbook_bid_vol_total is not None and q.orderbook_ask_vol_total is not None:
        imbalance = _factors.orderbook_imbalance(q.orderbook_bid_vol_total, q.orderbook_ask_vol_total)
    bid_series = [getattr(f.get("quote"), "orderbook_bid_vol_total", None) for f in frames if f.get("quote") is not None]
    seal = _factors.limit_up_seal_strength(q.indicative_price_yuan, q.price_limit_up_yuan,
                                           None, bid_series)
    return {
        "gap": _factors.gap_band_pass(gap) if gap is not None else None,
        "orderbook_imbalance": imbalance,
        "limit_up_seal": seal,
    }


def score_pool(frozen_pool, store, now):
    """用内存帧对全池增量评分，返回带 rank 的列表。"""
    items = []
    by_code = {it["code"]: it for it in frozen_pool.items}
    for code, pool_item in by_code.items():
        frames = store.frames(code)
        if not frames:
            continue
        q = frames[-1]["quote"]
        factors = _quote_factors(q, frames)
        auction, coverage, total = _score_item(pool_item, factors, CONFIG["branch"])
        items.append({
            "code": code, "name": q.name or pool_item.get("name", ""),
            "low_score": pool_item.get("low_score"),
            "auction_score": auction, "total_score": total, "coverage": coverage,
            "quality": getattr(q, "quality", "UNKNOWN"),
            "source": getattr(q, "source", ""),
            "source_timestamp": getattr(q, "source_timestamp", None),
            "rank": 0,
        })
    return _scoring.build_ranking(items, key="total_score", reverse=True)


def verify_final_quote(q, now, required_fields=None):
    """单票真实性门禁：必需字段 + 源时间不早于 09:25 + 字段能力已验证 + 质量非 INVALID。

    返回 {confirmed, missing, quote}。missing 为空表示 CONFIRMED。
    """
    required_fields = required_fields or CONFIG.get("required_final_fields", ["open_price"])
    missing = []
    if q is None:
        return {"confirmed": False, "missing": ["no_quote"], "quote": None}

    cutoff = now.replace(hour=9, minute=25, second=0, microsecond=0)
    # 只用源时间判定 09:25 后；received_at 只作审计，不得据此推定最终价量已确认
    src_after_cutoff = bool(q.source_timestamp) and str(q.source_timestamp) >= cutoff.isoformat()
    if not src_after_cutoff:
        missing.append("source_time_before_09:25")

    for f in required_fields:
        if f == "open_price":
            if q.final_open_price_yuan is None:
                # 9:25 后源报价的 price 才可作为最终开盘价证据，且必须显式赋给 final 字段
                if src_after_cutoff and q.indicative_price_yuan:
                    q.final_open_price_yuan = q.indicative_price_yuan
                else:
                    missing.append("open_price")
        elif f == "match_volume" and q.final_match_shares is None:
            missing.append("match_volume")

    if q.field_capability_version in ("", "unverified"):
        missing.append("field_capability_unverified")
    if getattr(q, "quality", "UNKNOWN") == "INVALID":
        missing.append("quality_invalid")

    return {"confirmed": not missing, "missing": missing, "quote": q}


def verify_final_quotes_per_code(payloads, now, required_fields=None):
    """逐票最终字段证据核验。payloads: [{code, quote, error}]。"""
    evidence = {}
    for p in payloads or []:
        code = str(p.get("code") or "").zfill(6)
        if p.get("error") or p.get("quote") is None:
            evidence[code] = {"confirmed": False, "missing": [p.get("error") or "no_quote"], "quote": None}
            continue
        evidence[code] = verify_final_quote(p["quote"], now, required_fields)
    return evidence


def _partial_threshold(n):
    return max(3, (n + 1) // 2)


def count_confirmed(top_n, evidence):
    return sum(1 for it in top_n if evidence.get(it["code"], {}).get("confirmed"))


def write_preliminary(ranking, now):
    """9:15-9:25 预测榜单（只读展示，不伪装最终）。"""
    from backend.services.snapshot_store import atomic_write_json
    payload = {
        "status": "completed", "phase": "PRELIMINARY",
        "now": now.isoformat(), "count": len(ranking),
        "items": ranking,
    }
    atomic_write_json(PRELIMINARY_FILE, payload)
    return payload


def _milestone_map(store):
    return {k: (v if isinstance(v, dict) else v.to_dict()) for k, v in store.milestones.items()}


def persist_milestone(run_id, key, state, evidence=None, error=None):
    """读现有 milestones_json -> merge -> SQL UPDATE（dispatcher/outcomes 更新 M4/M5 用）。"""
    if run_id is None:
        return None
    df = db_query("SELECT milestones_json FROM auction_runs WHERE id=:i", {"i": int(run_id)})
    milestones = {}
    if not df.empty and df.iloc[0]["milestones_json"]:
        try:
            milestones = json.loads(df.iloc[0]["milestones_json"])
        except json.JSONDecodeError:
            milestones = {}
    m = milestones.setdefault(key, {"key": key, "state": "pending"})
    m["state"] = state
    if evidence:
        m.setdefault("evidence", {}).update(evidence)
    if error:
        m["error"] = error
    update_run(run_id, milestones_json=json.dumps(milestones, ensure_ascii=False))
    return milestones


def _candidate_rows(run_id, ranked, evidence):
    rows = []
    for it in ranked:
        ev = evidence.get(it["code"], {})
        rows.append({
            "run_id": run_id, "code": it["code"], "name": it.get("name", ""),
            "low_score": json_safe(it.get("low_score")),
            "auction_score": json_safe(it.get("auction_score")), "total_score": json_safe(it.get("total_score")),
            "quality": it.get("quality"), "coverage": json_safe(it.get("coverage")),
            "tradability": "UNKNOWN", "source": it.get("source"),
            "source_timestamp": it.get("source_timestamp"),
            "confirmed_fields_json": json.dumps([] if ev.get("confirmed") else [], ensure_ascii=False),
            "evidence_json": json.dumps(json_safe({
                "confirmed": ev.get("confirmed", False),
                "missing_fields": ev.get("missing", []),
                "confirmation_reason": "final_quote_verified" if ev.get("confirmed") else "missing:" + ",".join(ev.get("missing", [])),
            }), ensure_ascii=False),
            "rank": it.get("rank"),
            "price": json_safe(it.get("price")),
            "last_close": json_safe(it.get("last_close")),
            "gap": json_safe(it.get("gap")),
            "volume_ratio": json_safe(it.get("volume_ratio")),
            "turnover": json_safe(it.get("turnover")),
        })
    return rows


def _finalize_items(top_n, evidence):
    """给 TopN 附逐票确认状态与缺失字段（保原位次）。"""
    out = []
    for it in top_n:
        ev = evidence.get(it["code"], {})
        out.append({
            **it,
            "item_confirmation": "CONFIRMED" if ev.get("confirmed") else "UNCONFIRMED",
            "original_rank": it.get("rank"),
            "missing_fields": ev.get("missing", []),
            "confirmation_reason": "final_quote_verified" if ev.get("confirmed") else "missing:" + ",".join(ev.get("missing", [])),
        })
    return out


def publish_final(run, ranked, top_n, evidence, overall, publication_kind, store, now, dry_run):
    """可恢复发布顺序：DB 候选+待发布事件持久化 → 原子快照可见 → outbox READY → run PUBLISHED。"""
    run_id = run["run_id"]
    snapshot_version = new_snapshot_version()
    ranking_version = new_snapshot_version()
    ranking_asof = now.isoformat()
    confirmed = count_confirmed(top_n, evidence)
    unconfirmed = len(top_n) - confirmed
    final_items = _finalize_items(top_n, evidence)

    payload = build_snapshot_payload(
        {"trade_date": run["trade_date"], "snapshot_version": snapshot_version,
         "branch": CONFIG["branch"]},
        final_items, phase="PUBLISHED", overall_confirmation=overall,
        publication_kind=publication_kind, ranking_version=ranking_version,
        ranking_asof=ranking_asof,
        pool={"asof_trading_day": run.get("pool_asof_trading_day")},
        counts={"pool_count": run.get("pool_count", 0), "scored_count": len(ranked),
                "confirmed_count": confirmed, "unconfirmed_count": unconfirmed},
        scope=CONFIG["final_scope"], strategy_version=run["strategy_version"],
        factor_spec_version=CONFIG["factor_spec_version"], config_hash=_config_hash(),
        code_commit=_code_commit(),
    )

    event_key = None
    if not dry_run:
        # 1. run PENDING + 候选持久化 + 待发布事件入队（检查写入结果，失败不得继续）
        if update_run_checked(run_id, state="PENDING_PUBLISH", phase="PENDING_PUBLISH",
                              snapshot_version=snapshot_version,
                              milestones_json=json.dumps(_milestone_map(store), ensure_ascii=False)) != 1:
            raise RuntimeError("run PENDING_PUBLISH 写入失败")
        db_delete("auction_candidates", {"run_id": run_id})
        cand_rows = _candidate_rows(run_id, ranked, evidence)
        n_cand = db_append("auction_candidates", cand_rows) if cand_rows else 0
        if ranked and n_cand <= 0:
            raise RuntimeError("候选写入失败，不得发布")
        if CONFIG.get("outbox_enabled") and CONFIG.get("preview_notify_enabled") and final_items:
            event_key = f"{run['trade_date']}|{run['strategy_version']}|{run['revision']}|{publication_kind}"
            from .notify import build_observation_email
            subject, text, html = build_observation_email(final_items, now, publication_kind, overall, CONFIG["top_n"])
            from .outbox import enqueue
            n_enq = enqueue(event_key, run_id, "email", "default",
                            {"subject": subject, "text": text, "html": html})
            if n_enq <= 0:
                logger.warning("outbox 入队失败 event_key=%s", event_key)

    if not dry_run:
        # 2. 原子快照可见
        publish_snapshot(payload)
        # 3. 快照可见后 outbox 转 READY（可发送）
        if event_key:
            from .outbox import promote_event_to_ready
            promote_event_to_ready(event_key)
        # 4. run 终态 PUBLISHED
        update_run_checked(run_id, state="PUBLISHED", phase="PUBLISHED", confirmed_at=now.isoformat(),
                           published_at=now.isoformat(),
                           milestones_json=json.dumps(_milestone_map(store), ensure_ascii=False))
    else:
        logger.info("dry_run: 跳过落库/快照/外发")
    return payload


def mark_unconfirmed(run, store, now, reason):
    update_run(run["run_id"], state="UNCONFIRMED", phase="UNCONFIRMED", last_error=reason,
               milestones_json=json.dumps(_milestone_map(store), ensure_ascii=False))
    return {"status": "unconfirmed", "reason": reason}


async def run_auction_poll_once(now, store, source, frozen_pool):
    """9:15–9:25 采集一轮：poll→validate→score→写 PRELIMINARY；不确认、不发布、不占 finalize。"""
    store.ensure_date(now)
    codes = [it["code"] for it in frozen_pool.items]
    payloads = await poll_pool_once(source, codes, CONFIG)
    valid = []
    for p in payloads:
        if p.get("quote") is None:
            continue
        q = p["quote"]
        q.trade_date = now.date().isoformat()
        q.market_phase = "PRE_AUCTION" if now.strftime("%H:%M") < "09:20" else "NON_CANCEL"
        qr = validate_quote(q, now.date().isoformat(), q.market_phase, None)
        q.quality = qr.quality
        q.quality_reasons = qr.reasons
        store.add_auction_frame(q.code, q, now)
        valid.append(q)
    ranking = score_pool(frozen_pool, store, now)
    preliminary = write_preliminary(ranking, now)
    return {"status": "polled", "polled": len(payloads), "valid": len(valid),
            "ranked": len(ranking), "preliminary": preliminary}


def score_final_pool(frozen_pool, final_quotes, now):
    """基于最终报价重算全冻结池评分（不复用竞价阶段旧帧）。final_quotes: {code: AuctionQuote}。"""
    items = []
    by_code = {it["code"]: it for it in frozen_pool.items}
    for code, q in (final_quotes or {}).items():
        pool_item = by_code.get(code, {})
        if q is None:
            continue
        factors = _quote_factors(q, [])
        auction, coverage, total = _score_item(pool_item, factors, CONFIG["branch"])
        price = getattr(q, "final_open_price_yuan", None) or getattr(q, "indicative_price_yuan", None)
        last_close = getattr(q, "prev_close_yuan", None)
        from . import factors as _f
        gap = _f.auction_gap(price, last_close) if price is not None and last_close else None
        items.append({
            "code": code, "name": q.name or pool_item.get("name", ""),
            "low_score": pool_item.get("low_score"),
            "auction_score": auction, "total_score": total, "coverage": coverage,
            "quality": getattr(q, "quality", "UNKNOWN"),
            "source": getattr(q, "source", ""),
            "source_timestamp": getattr(q, "source_timestamp", None),
            "price": json_safe(price),
            "last_close": json_safe(last_close),
            "gap": json_safe(gap),
            "volume_ratio": json_safe(getattr(q, "volume_ratio", None)),
            "turnover": json_safe(getattr(q, "turnover", None)),
            "trade_time": getattr(q, "source_timestamp", None),
            "rank": 0,
        })
    return _scoring.build_ranking(items, key="total_score", reverse=True)


async def finalize_once(trade_date, strategy_version, store, source, now, dry_run=False):
    """9:25 后最终确认：最终报价纳入重排 + 完整范围判定 + 部分确认保位次。

    dry_run=True 完全隔离：不 claim、不写生产 run/候选/outbox。
    """
    if dry_run:
        run = {"run_id": None, "trade_date": trade_date, "strategy_version": strategy_version,
               "revision": 1, "pool_asof_trading_day": None, "pool_count": 0}
    else:
        claim = claim_run_once(trade_date, strategy_version, 1)
        if not claim["claimed"]:
            return read_latest()
        run = {"run_id": claim["run_id"], "trade_date": trade_date,
               "strategy_version": strategy_version, "revision": 1,
               "pool_asof_trading_day": None, "pool_count": 0}

    frozen = getattr(store, "frozen_pool", None)
    if frozen is None:
        from .pool import freeze_pool
        frozen = freeze_pool(now.date())
        if frozen.status != "completed":
            if not dry_run:
                update_run_checked(run["run_id"], state="SKIPPED_NO_POOL", phase=frozen.status,
                                   last_error=frozen.reason,
                                   milestones_json=json.dumps(_milestone_map(store), ensure_ascii=False))
            return {"status": "empty_pool", "reason": frozen.reason}
    store.frozen_pool = frozen
    run["pool_asof_trading_day"] = getattr(frozen, "asof_trading_day", None)
    run["pool_count"] = getattr(frozen, "total_count", 0)

    if not dry_run:
        update_run_checked(run["run_id"], state="WAITING_FINAL", phase="WAITING_FINAL",
                           pool_hash=getattr(frozen, "pool_hash", None))
    # 六里程碑：M0 产池 / M1 冻结 / M2 采集统计（用当日已采集帧）
    store.set_milestone("M0", "done", evidence={
        "asof": getattr(frozen, "asof_trading_day", None),
        "pool_hash": getattr(frozen, "pool_hash", None),
        "total_count": getattr(frozen, "total_count", 0)}, now=now)
    store.set_milestone("M1", "done", evidence={"pool_hash": getattr(frozen, "pool_hash", None)}, now=now)
    store.set_milestone("M2", "done", evidence={
        "codes_with_frames": len([c for c in store.auction_frames if store.frames(c)])}, now=now)
    store.set_milestone("M3", "started", now=now)
    codes = [it["code"] for it in frozen.items]
    required = CONFIG.get("required_final_fields", ["open_price"])
    top_n = int(CONFIG["top_n"])

    started = time.monotonic()
    confirm_deadline = started + CONFIG["final_confirm_deadline_s"]
    last_evidence, last_ranking = None, None
    last_scope_complete = False
    while time.monotonic() < confirm_deadline:
        attempt_deadline = min(time.monotonic() + CONFIG["final_attempt_budget_s"], confirm_deadline)
        payloads = await poll_pool_once(source, codes, CONFIG, deadline=attempt_deadline)
        final_quotes = {p["code"]: p["quote"] for p in payloads
                        if p.get("quote") is not None and p.get("error") is None}
        evidence = verify_final_quotes_per_code(payloads, now, required)
        # 完整范围 = 冻结池全部 code 都有逐票结果（含 no_quote），不以 pool_max 代替
        covered = set(evidence.keys())
        missing_codes = {it["code"] for it in frozen.items} - covered
        scope_complete = not missing_codes
        # 最终报价参与重排（不是旧帧）
        ranking = score_final_pool(frozen, final_quotes, now)
        last_evidence, last_ranking, last_scope_complete = evidence, ranking, scope_complete
        top = ranking[: top_n]
        all_confirmed = bool(top) and all(evidence.get(it["code"], {}).get("confirmed") for it in top)
        if all_confirmed and scope_complete:
            payload = publish_final(run, ranking, top, evidence, "CONFIRMED", "OBSERVATION_ONLY",
                                    store, now, dry_run)
            store.set_milestone("M3", "done", evidence={"overall": "CONFIRMED"}, now=now)
            store.set_milestone("M4", "done",
                                evidence={"snapshot_version": payload.get("snapshot_version")}, now=now)
            return payload
        await asyncio.sleep(CONFIG["final_retry_interval_s"])

    # 确认截止：仅完整范围才允许部分确认观察清单
    if last_ranking and last_scope_complete:
        top = last_ranking[: top_n]
        confirmed = count_confirmed(top, last_evidence or {})
        if confirmed >= _partial_threshold(len(top)):
            payload = publish_final(run, last_ranking, top, last_evidence or {},
                                    "PARTIAL_CONFIRMED", "OBSERVATION_ONLY", store, now, dry_run)
            store.set_milestone("M3", "done",
                                evidence={"overall": "PARTIAL_CONFIRMED", "confirmed": confirmed}, now=now)
            return payload
    if not dry_run:
        return mark_unconfirmed(run, store, now, "final_market_data_not_verified")
    return {"status": "unconfirmed", "reason": "final_market_data_not_verified"}


async def run_auction_radar_once(now=None, dry_run=False, force=False, store=None, source=None):
    """便捷单轮：产池→冻结→采集→最终确认（CLI/trigger 用）。"""
    now = now or _now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=BEIJING_TZ)
    store = store or AuctionStore()
    store.ensure_date(now)

    pool = prepare_low_pool(now.date(), allow_regenerate=True)
    if pool.status != "completed":
        return {"status": "empty_pool", "reason": pool.reason}
    frozen = freeze_pool(now.date())
    if frozen.status != "completed":
        return {"status": "stale_pool", "reason": frozen.reason}
    source = source or TencentAuctionSource()

    if now.strftime("%H:%M") <= "09:25":
        result = await run_auction_poll_once(now, store, source, frozen)
        result["status"] = "polled"
        return result
    return await finalize_once(now.date().isoformat(), CONFIG["strategy_version"], store, source, now, dry_run)


def recover_incomplete_publications():
    from .outbox import recover_sending
    return {"status": "completed", "recovered_sending": recover_sending()}
