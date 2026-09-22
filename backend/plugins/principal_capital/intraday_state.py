"""23 v2 日内状态模型与纯函数（无网络 / 无文件 I/O 的核心部分）。

状态文件: data/principal_capital_intraday_state.json（唯一临时文件 + os.replace 原子写）。
文件按交易日重置，只保留当前交易日的候选、池、同源累计序列、摘要标记与审计游标。

本模块拆成两层：
  - 纯函数（可独立单测）：merge_candidate_state / append_fund_observation /
    compute_intraday_features / select_radar_pool / should_run_sentinel /
    should_finalize_session / acquire_owner / reset_state_for_trade_date。
  - 薄 I/O 层：load_state / save_state。
"""
import fcntl
import json
import math
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .config import CONFIG, INTRADAY_STATE_FILE

BEIJING_TZ = timezone(timedelta(hours=8))
SCHEMA_VERSION = 2

_DIRECTION_BUY = "buy"
_DIRECTION_SELL = "sell"

# 候选最新指标中保留的字段（稳定、可 JSON 序列化）。
_METRIC_KEYS = (
    "name", "price", "change_pct", "total_amount", "main_net_inflow",
    "main_inflow_ratio", "super_net", "big_net", "mid_net", "small_net", "source",
)


def _json_safe(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _finite(value):
    if value is None or value == "" or value == "-":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _ensure_aware(value: datetime) -> datetime:
    """v2 契约：naive datetime 直接抛 ValueError，不得自动补时区掩盖调用错误。"""
    if getattr(value, "tzinfo", None) is None:
        raise ValueError("datetime 必须带时区（请显式传北京时区）")
    return value


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return _ensure_aware(value)
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return _ensure_aware(parsed)


def _to_iso(value) -> str:
    if isinstance(value, datetime):
        return _ensure_aware(value).isoformat()
    return str(value)


def normalize_code(value) -> str:
    code = str(value or "").strip().zfill(6)
    return code if code.isdigit() and len(code) == 6 else ""


def empty_state(
    trade_date: str,
    owner_id: Optional[str] = None,
    owner_lease_expires_at: Optional[str] = None,
    pipeline_mode: str = "strict",
) -> dict:
    """返回全新日内状态骨架。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "trade_date": trade_date,
        "owner_id": owner_id,
        "owner_lease_expires_at": owner_lease_expires_at,
        "last_batch_id": None,
        "pipeline_mode": pipeline_mode,
        "auto_fallback": None,
        "summary_state": {"am": {"status": "not_attempted"}, "pm": {"status": "not_attempted"}},
        "summary_attempted_at": {},
        "summary_sent_at": {},
        "audit_cursor": 0,
        "sentinel_done": [],
        "candidates": {},
        "pool_entries": {},
        "fund_series": {},
    }


def reset_state_for_trade_date(state: Optional[dict], trade_date: str) -> dict:
    """跨交易日清空候选、序列、摘要标记和审计游标。输入不原地修改。"""
    if not isinstance(state, dict) or state.get("trade_date") != trade_date:
        return empty_state(
            trade_date,
            pipeline_mode=(state or {}).get("pipeline_mode", "strict"),
        )
    result = dict(state)
    result.setdefault("schema_version", SCHEMA_VERSION)
    result.setdefault("summary_state", {"am": {"status": "not_attempted"}, "pm": {"status": "not_attempted"}})
    result.setdefault("summary_attempted_at", {})
    result.setdefault("summary_sent_at", {})
    result.setdefault("audit_cursor", 0)
    result.setdefault("sentinel_done", [])
    result.setdefault("candidates", {})
    result.setdefault("pool_entries", {})
    result.setdefault("fund_series", {})
    result.setdefault("pipeline_mode", state.get("pipeline_mode", "strict"))
    result.setdefault("auto_fallback", None)
    return result


# --------------------------------------------------------------------------- #
# 唯一写者
# --------------------------------------------------------------------------- #

def acquire_owner(state: dict, owner_id: str, lease_seconds: int, now: datetime) -> tuple:
    """official 启动时抢占/续租 owner。返回 (ok, new_state, reason)。

    - 无 owner 或同 owner：续租成功。
    - 已有不同 owner 且租约未过期：owner_conflict，拒绝写入。
    """
    now = _ensure_aware(now)
    result = dict(state)
    existing = result.get("owner_id")
    if existing and existing != owner_id:
        expires = _parse_dt(result.get("owner_lease_expires_at"))
        if expires and expires > now:
            return False, result, f"owner_conflict: {existing}"
    result["owner_id"] = owner_id
    result["owner_lease_expires_at"] = (now + timedelta(seconds=int(lease_seconds))).isoformat()
    return True, result, None


def release_owner(state: dict) -> dict:
    """session finalizer 完成后主动释放租约（保留 owner_id 便于审计）。"""
    result = dict(state)
    result["owner_lease_expires_at"] = None
    return result


def acquire_owner_atomic(path, owner_id: str, lease_seconds: int, now: datetime) -> tuple:
    """P0-R4：在文件锁临界区内完成 load → 检查 lease → 写入新 lease。

    返回 (ok, state, reason)。lease 持久化完成后才返回，调用方随后才能开始网络请求。
    """
    now = _ensure_aware(now)
    file_path = _state_path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = file_path.with_suffix(file_path.suffix + ".lock")
    with open(lock_path, "a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            state = load_state(path=file_path, trade_date=now.date().isoformat(), now=now)
            ok, state, reason = acquire_owner(state, owner_id, lease_seconds, now)
            if ok:
                save_state(state, path=file_path)
            return ok, state, reason
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def mark_sentinel_done(state: dict, label: str) -> dict:
    result = dict(state)
    done = list(result.get("sentinel_done") or [])
    if label not in done:
        done.append(label)
    result["sentinel_done"] = done
    return result


def _set_summary_state(state: dict, session: str, status: str, at_iso: str = None, attempt_id: str = None) -> dict:
    result = dict(state)
    default = {"am": {"status": "not_attempted"}, "pm": {"status": "not_attempted"}}
    summary_state = {s: dict(v) for s, v in (result.get("summary_state") or default).items()}
    entry = summary_state.setdefault(session, {"status": "pending"})
    entry["status"] = status
    if at_iso is not None:
        entry["updated_at"] = at_iso
    if attempt_id is not None:
        entry["attempt_id"] = attempt_id
    result["summary_state"] = summary_state
    return result


def mark_summary_pending(state: dict, session: str, at_iso: str, attempt_id: str) -> dict:
    """SMTP 前原子落盘 pending + attempt_id（P0-5 投递状态机）。"""
    result = _set_summary_state(state, session, "pending", at_iso, attempt_id)
    attempted = dict(result.get("summary_attempted_at") or {})
    attempted[session] = at_iso
    result["summary_attempted_at"] = attempted
    return result


def mark_summary_sent(state: dict, session: str, at_iso: str) -> dict:
    result = _set_summary_state(state, session, "sent", at_iso)
    sent_at = dict(result.get("summary_sent_at") or {})
    sent_at[session] = at_iso
    result["summary_sent_at"] = sent_at
    return result


def mark_summary_explicit_failed(state: dict, session: str, at_iso: str) -> dict:
    return _set_summary_state(state, session, "explicit_failed", at_iso)


def mark_summary_delivery_unknown(state: dict, session: str, at_iso: str) -> dict:
    return _set_summary_state(state, session, "delivery_unknown", at_iso)


# --------------------------------------------------------------------------- #
# 纯函数：候选状态合并
# --------------------------------------------------------------------------- #

def _candidate_metrics(row: dict) -> dict:
    return _json_safe({key: row.get(key) for key in _METRIC_KEYS})


def merge_candidate_state(
    previous: Optional[dict],
    current_buy: list,
    current_sell: list,
    batch_meta: dict,
) -> dict:
    """合并本批候选到当日候选状态。返回新的 candidates 字典（不原地修改）。

    - 首次出现的键 fresh=true；已存在 fresh=false。
    - 完整批次未命中的键 is_current=false；partial 批次不据此驱逐。
    - 同 batch_id 重放幂等：不重复增加 seen_rounds。
    """
    previous_candidates = (previous or {}).get("candidates") if isinstance(previous, dict) else (previous or {})
    candidates = {key: dict(entry) for key, entry in (previous_candidates or {}).items()}
    now_iso = _to_iso(batch_meta.get("now"))
    batch_id = str(batch_meta.get("batch_id") or uuid.uuid4().hex)
    is_partial = bool(batch_meta.get("is_partial", False))

    seen = set()
    for direction, rows in ((_DIRECTION_BUY, current_buy), (_DIRECTION_SELL, current_sell)):
        for row in rows or []:
            code = normalize_code(row.get("code"))
            if not code:
                continue
            key = f"{direction}:{code}"
            seen.add(key)
            entry = candidates.get(key)
            if entry is None:
                candidates[key] = {
                    "first_seen_at": now_iso,
                    "last_seen_at": now_iso,
                    "last_batch_id": batch_id,
                    "seen_rounds": 1,
                    "is_current": True,
                    "latest_metrics": _candidate_metrics(row),
                    "quality_status": row.get("quality_status", "provisional"),
                    "fresh": True,
                }
                continue
            # 同 batch 重放幂等
            if entry.get("last_batch_id") == batch_id:
                candidates[key] = entry
                continue
            entry["last_seen_at"] = now_iso
            entry["last_batch_id"] = batch_id
            entry["seen_rounds"] = int(entry.get("seen_rounds", 0)) + 1
            entry["is_current"] = True
            entry["latest_metrics"] = _candidate_metrics(row)
            entry["quality_status"] = row.get("quality_status", entry.get("quality_status", "provisional"))
            entry["fresh"] = False
            candidates[key] = entry

    if not is_partial:
        for key, entry in list(candidates.items()):
            if key not in seen and entry.get("is_current"):
                entry["is_current"] = False
                candidates[key] = entry
    return candidates


def current_candidate_lists(state: Optional[dict]) -> tuple:
    """从日内状态导出报告用的 current/today 列表（稳定排序）。"""
    candidates = (state or {}).get("candidates") or {}
    buy_current, sell_current, buy_today, sell_today = [], [], [], []
    for key, entry in candidates.items():
        if ":" not in key:
            continue
        direction, code = key.split(":", 1)
        metrics = dict(entry.get("latest_metrics") or {})
        row = {"code": code, **metrics}
        if direction == _DIRECTION_BUY:
            buy_today.append(dict(row))
            if entry.get("is_current"):
                buy_current.append(dict(row))
        else:
            sell_today.append(dict(row))
            if entry.get("is_current"):
                sell_current.append(dict(row))
    for lst in (buy_current, sell_current, buy_today, sell_today):
        lst.sort(key=lambda item: str(item.get("code")))
    return buy_current, sell_current, buy_today, sell_today


# --------------------------------------------------------------------------- #
# 纯函数：同源日内资金特征
# --------------------------------------------------------------------------- #

def _obs_eligible(obs: dict) -> bool:
    if obs.get("partial") or obs.get("stale") or obs.get("cache"):
        return False
    return _finite(obs.get("main_net_inflow")) is not None and _finite(obs.get("total_amount")) is not None


def append_fund_observation(series: dict, row: dict, batch_meta: dict, cfg: dict) -> dict:
    """给某 code 追加一条同源累计快照。返回新的 fund_series 字典（不原地修改）。

    按 code + batch_id 幂等：同 batch 重放不重复追加。
    """
    result = {code: list(items) for code, items in (series or {}).items()}
    code = normalize_code(row.get("code"))
    if not code:
        return result
    max_points = int((cfg or {}).get("intraday_max_points", CONFIG["intraday_max_points"]))
    observation = {
        "observed_at": _to_iso(batch_meta.get("observed_at") or batch_meta.get("now")),
        "source_segment": batch_meta.get("source_segment") or f"{row.get('source', 'sina_single')}:v1:{batch_meta.get('trade_date', '')}",
        "main_net_inflow": _finite(row.get("main_net_inflow")),
        "total_amount": _finite(row.get("total_amount")),
        "batch_id": str(batch_meta.get("batch_id") or ""),
        "partial": bool(batch_meta.get("is_partial", False)),
        "stale": bool(batch_meta.get("is_stale", False)),
        "cache": bool(batch_meta.get("is_cache", False)),
    }
    items = result.get(code, [])
    if items and items[-1].get("batch_id") == observation["batch_id"]:
        return result  # 同 batch 幂等
    items.append(observation)
    result[code] = items[-max_points:]
    return result


def _warm(reason: str) -> dict:
    return {
        "roll_net_30m": None,
        "acc_win": 0,
        "inc_ratio_5m": None,
        "interval_seconds": None,
        "warming": True,
        "warming_reason": reason,
    }


def compute_intraday_features(series: list, now: datetime, cfg: dict = None) -> dict:
    """对同一 code 的累计快照序列计算 5 分钟增量 / 30 分钟滚动特征。

    只对同源、同交易日、连续、且 total_amount 不回退的相邻快照差分。
    间隔不在 [2,10] 分钟、切源、累计值回退、跨日均开新段并 warming。
    """
    del cfg
    now = _ensure_aware(now)
    if not series:
        return _warm("first_observation")
    points = []
    for obs in series:
        ts = _parse_dt(obs.get("observed_at"))
        if ts is None or ts.date() != now.date():
            continue
        points.append((ts, obs))
    points.sort(key=lambda item: item[0])
    if len(points) < 2:
        return _warm("first_observation")

    pairs = []
    for index in range(len(points) - 2, -1, -1):
        prev_ts, prev = points[index]
        cur_ts, cur = points[index + 1]
        # invalid/partial/cache/stale 必须中断连续段（不能先过滤后跨越该点配对）
        if not _obs_eligible(prev) or not _obs_eligible(cur):
            break
        if prev.get("source_segment") != cur.get("source_segment"):
            break  # 切源：开新段，之前的历史不再与本段拼接
        delta_seconds = (cur_ts - prev_ts).total_seconds()
        if delta_seconds < 120 or delta_seconds > 600:
            break
        prev_total = _finite(prev.get("total_amount"))
        cur_total = _finite(cur.get("total_amount"))
        if prev_total is None or cur_total is None or cur_total < prev_total:
            break  # 累计值回退：开新段
        pairs.append({
            "delta_main_net": _finite(cur.get("main_net_inflow")) - _finite(prev.get("main_net_inflow")),
            "delta_total_amount": cur_total - prev_total,
            "interval_seconds": delta_seconds,
            "prev_at": prev_ts,
            "at": cur_ts,
        })
    pairs.reverse()
    if not pairs:
        # 无法构成任何有效相邻差分；给出最近一次导致开新段的原因
        prev_ts, prev = points[-2]
        cur_ts, cur = points[-1]
        if not _obs_eligible(prev) or not _obs_eligible(cur):
            return _warm("batch_partial")
        if prev.get("source_segment") != cur.get("source_segment"):
            return _warm("source_changed")
        delta_seconds = (cur_ts - prev_ts).total_seconds()
        if delta_seconds > 600:
            return _warm("gap_too_large")
        if delta_seconds < 120:
            return _warm("interval_too_small")
        if _finite(cur.get("total_amount")) is not None and _finite(prev.get("total_amount")) is not None \
                and _finite(cur.get("total_amount")) < _finite(prev.get("total_amount")):
            return _warm("counter_reset")
        return _warm("first_observation")

    result = {
        "roll_net_30m": None,
        "acc_win": 0,
        "inc_ratio_5m": None,
        "interval_seconds": None,
        "warming": False,
        "warming_reason": None,
    }

    # acc_win：从最新值向前数连续 delta_main_net > 0 的有效窗口数
    acc = 0
    for pair in reversed(pairs):
        if pair["delta_main_net"] > 0:
            acc += 1
        else:
            break
    result["acc_win"] = acc

    latest = pairs[-1]
    if latest["delta_total_amount"] > 0:
        result["inc_ratio_5m"] = round(latest["delta_main_net"] / latest["delta_total_amount"] * 100, 4)
        result["interval_seconds"] = int(latest["interval_seconds"])

    cutoff = now - timedelta(minutes=30)
    recent = [pair for pair in pairs if pair["at"] >= cutoff]
    if not recent:
        result["roll_net_30m"] = None
        result["warming"] = True
        result["warming_reason"] = "insufficient_coverage"
        return result

    recent_times = sorted({pair["prev_at"] for pair in recent} | {recent[-1]["at"]})
    span_seconds = (recent_times[-1] - recent_times[0]).total_seconds()
    max_gap = max(
        (recent_times[i + 1] - recent_times[i]).total_seconds()
        for i in range(len(recent_times) - 1)
    ) if len(recent_times) > 1 else 0.0
    if span_seconds < 25 * 60 or max_gap > 600:
        result["roll_net_30m"] = None
        result["warming"] = True
        result["warming_reason"] = "insufficient_coverage"
        return result

    result["roll_net_30m"] = round(sum(pair["delta_main_net"] for pair in recent), 2)
    return result


# --------------------------------------------------------------------------- #
# 纯函数：雷达池选择
# --------------------------------------------------------------------------- #

def _pool_score(entry: dict) -> tuple:
    metrics = entry.get("latest_metrics") or {}
    ratio = _finite(metrics.get("main_inflow_ratio")) or 0.0
    net = _finite(metrics.get("main_net_inflow")) or 0.0
    return (-ratio, -net, str(entry.get("code") or ""))


def _fresh_score(row: dict) -> tuple:
    ratio = _finite(row.get("main_inflow_ratio")) or 0.0
    net = _finite(row.get("main_net_inflow")) or 0.0
    return (-ratio, -net, str(normalize_code(row.get("code")) or ""))


def _pool_stale(entry: dict, now: datetime, max_stale_min: int) -> bool:
    last_seen = _parse_dt(entry.get("last_seen_at"))
    if last_seen is None:
        return True
    return (now - last_seen).total_seconds() > int(max_stale_min) * 60


def _pool_in_dwell(entry: dict, now: datetime, min_dwell_min: int) -> bool:
    dwell_until = _parse_dt(entry.get("dwell_until"))
    return dwell_until is not None and dwell_until > now


def select_radar_pool(previous_pool: Optional[dict], current_buy: list, now: datetime, cfg: dict) -> dict:
    """从本轮完整 buy_candidates_current 选择吸筹观察池。

    - current_buy=None 表示 partial 批次：不驱逐旧成员，只移除超过最大陈旧期限者。
    - 完整批次：保留驻留期内的旧成员（上限 protected_cap，且必须给轮换席让路），
      剩余席位按 fresh 候选稳定排序填入。
    """
    now = _ensure_aware(now)
    cfg = cfg or {}
    previous = previous_pool or {}
    max_n = int(cfg.get("radar_pool_max", 40))
    min_dwell = int(cfg.get("radar_pool_min_dwell_min", 30))
    protected_cap = int(cfg.get("radar_pool_protected_cap", 30))
    rotation = int(cfg.get("radar_pool_rotation_seats", 10))
    max_stale = int(cfg.get("radar_pool_max_stale_min", 15))

    if current_buy is None:
        return {
            code: dict(entry)
            for code, entry in previous.items()
            if not _pool_stale(entry, now, max_stale)
        }

    buy_map = {normalize_code(row.get("code")): row for row in current_buy if normalize_code(row.get("code"))}
    protected = []
    for code, entry in previous.items():
        if not _pool_stale(entry, now, max_stale) and _pool_in_dwell(entry, now, min_dwell):
            protected.append((code, entry))
    protected.sort(key=lambda item: _pool_score(item[1]))

    keep_protected = min(len(protected), protected_cap, max(0, max_n - rotation))
    kept = protected[:keep_protected]
    kept_codes = {code for code, _ in kept}

    fresh = []
    for code, row in buy_map.items():
        if code in kept_codes:
            continue
        fresh.append((code, row))
    fresh.sort(key=lambda item: _fresh_score(item[1]))

    remaining = max_n - len(kept)
    result = {}
    for code, entry in kept:
        item = dict(entry)
        item["selection_reason"] = "protected_dwell"
        # 驻留成员若本轮仍在候选里，刷新其最新指标供雷达评分
        if code in buy_map:
            item["latest_metrics"] = _candidate_metrics(buy_map[code])
        result[code] = item
    for code, row in fresh[:remaining]:
        previous_entry = previous.get(code)
        result[code] = {
            "code": code,
            "entered_at": previous_entry.get("entered_at") if previous_entry else now.isoformat(),
            "last_seen_at": now.isoformat(),
            "dwell_until": (now + timedelta(minutes=min_dwell)).isoformat(),
            "selection_reason": "rotation" if previous_entry else "fresh",
            "source_batch_id": row.get("_batch_id") or row.get("batch_id"),
            "latest_metrics": _candidate_metrics(row),
        }
    return result


# --------------------------------------------------------------------------- #
# 纯函数：哨兵与 finalizer
# --------------------------------------------------------------------------- #

def should_run_sentinel(state: dict, now: datetime, sentinel_times: list) -> Optional[str]:
    """返回本轮应当执行的最晚一个未执行的哨兵时点；无则 None。"""
    now = _ensure_aware(now)
    done = set((state or {}).get("sentinel_done") or [])
    now_hm = now.astimezone(BEIJING_TZ).strftime("%H:%M")
    due = [item for item in (sentinel_times or []) if item <= now_hm and item not in done]
    if not due:
        return None
    return max(due)


def should_finalize_session(state: dict, session: str, latest_batch: dict, now: datetime, cfg: dict) -> dict:
    """判断午间/收盘摘要是否应发送。返回 {should_send, reason, skipped_reason}。

    P0-5：除 completed/年龄外，还校验投递状态机、quality.notify_eligible、
    trade_date 与 batch_id 一致性；delivery_unknown / explicit_failed 不自动重发。
    """
    now = _ensure_aware(now)
    cfg = cfg or {}
    summary_entry = ((state or {}).get("summary_state") or {}).get(session) or {}
    status = summary_entry.get("status", "not_attempted")
    if status == "sent":
        return {"should_send": False, "reason": "already_sent", "skipped_reason": None}
    if status == "delivery_unknown":
        return {"should_send": False, "reason": "delivery_unknown", "skipped_reason": "delivery_unknown"}
    if status == "pending" and summary_entry.get("attempt_id"):
        # P0-R3：pending + attempt_id 已落盘 -> 上次投递结果不明，禁止自动重发
        return {"should_send": False, "reason": "delivery_unknown", "skipped_reason": "pending_attempt_recovered"}
    if status == "explicit_failed":
        # 明确失败允许显式手工重试；自动 finalizer 不自动重发
        return {"should_send": False, "reason": "explicit_failed", "skipped_reason": "explicit_failed"}
    if not latest_batch or latest_batch.get("status") != "completed":
        return {"should_send": False, "reason": "no_complete_batch", "skipped_reason": "no_complete_batch"}
    quality = latest_batch.get("quality") or {}
    if not quality.get("notify_eligible"):
        return {"should_send": False, "reason": "notify_not_eligible", "skipped_reason": "notify_not_eligible"}
    if latest_batch.get("trade_date") and latest_batch.get("trade_date") != now.date().isoformat():
        return {"should_send": False, "reason": "trade_date_mismatch", "skipped_reason": "trade_date_mismatch"}
    if (state or {}).get("last_batch_id") and latest_batch.get("batch_id") != (state or {}).get("last_batch_id"):
        return {"should_send": False, "reason": "batch_mismatch", "skipped_reason": "batch_mismatch"}
    ts = _parse_dt(latest_batch.get("now"))
    max_age_min = int(cfg.get("summary_max_age_min", CONFIG["summary_max_age_min"]))
    if ts is None or (now - ts).total_seconds() > max_age_min * 60:
        return {"should_send": False, "reason": "latest_batch_stale", "skipped_reason": "latest_batch_stale"}
    return {"should_send": True, "reason": "ready", "skipped_reason": None}


# --------------------------------------------------------------------------- #
# 薄 I/O 层
# --------------------------------------------------------------------------- #

def _state_path(path=None) -> Path:
    return Path(path) if path is not None else INTRADAY_STATE_FILE


def load_state(path=None, trade_date: Optional[str] = None, now: Optional[datetime] = None) -> dict:
    """读取日内状态；跨交易日自动重置；损坏时备份损坏文件并从空状态 warming。"""
    now = _ensure_aware(now) if now else datetime.now(BEIJING_TZ)
    trade_date = trade_date or now.date().isoformat()
    file_path = _state_path(path)
    state = None
    valid = False
    source_unavailable = not file_path.exists()
    if file_path.exists():
        try:
            state = json.loads(file_path.read_text(encoding="utf-8"))
            valid = isinstance(state, dict) and state.get("schema_version") == SCHEMA_VERSION
        except (json.JSONDecodeError, OSError):
            state = None
            valid = False
        if not valid:
            # P2-5：损坏状态备份后从空状态 warming，不静默丢弃
            try:
                stamp = datetime.now(BEIJING_TZ).strftime("%Y%m%dT%H%M%S")
                file_path.replace(file_path.with_suffix(file_path.suffix + f".corrupt.{stamp}"))
            except OSError:
                pass
    if not valid:
        state = empty_state(trade_date, pipeline_mode=CONFIG["pipeline_mode"])
        if source_unavailable:
            state["_source_unavailable"] = True
        else:
            state["warming_reason"] = "corrupt_state_recovered"
    return reset_state_for_trade_date(state, trade_date)


def clean_state_for_save(state: dict) -> dict:
    """P1-R3：保存前 schema 清理，只保留正式字段，剔除 _source_unavailable / warming_reason。"""
    return {
        key: value for key, value in (state or {}).items()
        if not key.startswith("_") and key != "warming_reason"
    }


def save_state(state: dict, path=None) -> None:
    """唯一临时文件 + os.replace 原子写；自动剔除临时诊断标记。"""
    file_path = _state_path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_suffix(f".tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
    payload = _json_safe(clean_state_for_save(state))
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(tmp_path, file_path)
