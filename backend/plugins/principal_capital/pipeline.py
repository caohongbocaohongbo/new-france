"""23 v2 流水线编排：RoundContext、候选并集、shadow 真值对照、哨兵回退判定。

所有集合函数按 code 稳定排序，输入 dict 不原地修改，不读取全局时间。
"""
import math
import uuid
from datetime import timedelta, timezone

from .config import CONFIG
from .intraday_state import _finite, normalize_code

BEIJING_TZ = timezone(timedelta(hours=8))


def build_round_context(
    owner_id: str,
    execution_mode: str,
    pipeline_mode: str,
    trade_date: str,
    started_at,
    deadline_at,
) -> dict:
    """一次扫描唯一 batch。started_at/deadline_at 必须为 aware datetime。"""
    for name, value in (("started_at", started_at), ("deadline_at", deadline_at)):
        if getattr(value, "tzinfo", None) is None:
            raise ValueError(f"{name} 必须带时区")
    return {
        "batch_id": uuid.uuid4().hex,
        "owner_id": owner_id,
        "execution_mode": execution_mode,
        "pipeline_mode": pipeline_mode,
        "trade_date": trade_date,
        "started_at": started_at.isoformat(),
        "deadline_at": deadline_at.isoformat(),
    }


def build_coarse_union(rows, previous_codes, dwell_codes, audit_codes, cfg) -> dict:
    """hybrid 粗筛候选并集（多条件 ∪ 上一轮 current ∪ 驻留 ∪ 补集审计分片）。

    ratioamount/r0_ratio 已是百分数（parse_bulk_rows 只标准化一次），此处不再二次 *100。
    """
    cfg = cfg or {}
    buy_ratio = float(cfg.get("coarse_buy_ratio", CONFIG["coarse_buy_ratio"]))
    sell_ratio = float(cfg.get("coarse_sell_ratio", CONFIG["coarse_sell_ratio"]))
    r0_head = int(cfg.get("coarse_r0_head", CONFIG["coarse_r0_head"]))

    reasons = {}

    def add(code, reason):
        code = normalize_code(code)
        if not code:
            return
        reasons.setdefault(code, set()).add(reason)

    eligible = [
        row for row in (rows or [])
        if isinstance(row, dict) and "coarse_candidate" in (row.get("eligible_for") or [])
    ]

    for row in eligible:
        code = normalize_code(row.get("code"))
        ratio = _finite(row.get("ratioamount"))
        if ratio is not None and ratio >= buy_ratio:
            add(code, "ratioamount_buy")
        if ratio is not None and ratio <= sell_ratio:
            add(code, "ratioamount_sell")

    by_net = sorted(
        [row for row in eligible if _finite(row.get("super_net")) is not None],
        key=lambda row: _finite(row["super_net"]),
        reverse=True,
    )
    by_ratio = sorted(
        [row for row in eligible if _finite(row.get("super_ratio")) is not None],
        key=lambda row: _finite(row["super_ratio"]),
        reverse=True,
    )
    for row in by_net[:r0_head]:
        add(row.get("code"), "r0_net_top")
    for row in by_net[-r0_head:] if r0_head else []:
        add(row.get("code"), "r0_net_bottom")
    for row in by_ratio[:r0_head]:
        add(row.get("code"), "r0_ratio_top")
    for row in by_ratio[-r0_head:] if r0_head else []:
        add(row.get("code"), "r0_ratio_bottom")

    for code in (previous_codes or []):
        add(code, "previous_current")
    for code in (dwell_codes or []):
        add(code, "dwell")
    for code in (audit_codes or []):
        add(code, "complement_audit")

    codes = sorted(reasons.keys())
    return {"codes": codes, "reasons": {code: sorted(reason_set) for code, reason_set in reasons.items()}}


def _df_codes(df) -> set:
    if df is None or getattr(df, "empty", True) or "code" not in getattr(df, "columns", []):
        return set()
    return {normalize_code(code) for code in df["code"].tolist()}


def compare_with_truth(coarse_codes, full_refined_df, filters) -> dict:
    """同一批逐票真值上做 bulk 漏斗集合 diff（禁止跨 batch 比较）。"""
    coarse = set(normalize_code(code) for code in (coarse_codes or []))
    truth_buy = _df_codes(filters["buy"](full_refined_df))
    truth_sell = _df_codes(filters["sell"](full_refined_df))
    if full_refined_df is None or full_refined_df.empty:
        shadow_buy, shadow_sell = set(), set()
    else:
        shadow_df = full_refined_df[full_refined_df["code"].map(lambda code: normalize_code(code) in coarse)]
        shadow_buy = _df_codes(filters["buy"](shadow_df))
        shadow_sell = _df_codes(filters["sell"](shadow_df))
    return {
        "kind": "shadow_truth",
        "truth_buy_count": len(truth_buy),
        "truth_sell_count": len(truth_sell),
        "false_negative_buy": sorted(truth_buy - shadow_buy),
        "false_negative_sell": sorted(truth_sell - shadow_sell),
        "false_positive_buy": sorted(shadow_buy - truth_buy),
        "false_positive_sell": sorted(shadow_sell - truth_sell),
    }


def evaluate_auto_fallback(audit, bulk_validation, refine_coverage_ratio, deadline_met) -> dict:
    """任一哨兵/覆盖/死线条件满足即触发 strict 自动回退。"""
    reasons = []
    if audit and (audit.get("false_negative_buy") or audit.get("false_negative_sell")):
        reasons.append("sentinel_false_negative")
    if bulk_validation is not None and not bulk_validation.get("valid"):
        reasons.append("bulk_coverage")
    if refine_coverage_ratio is not None and refine_coverage_ratio < 1.0:
        reasons.append("refine_coverage")
    if deadline_met is False:
        reasons.append("deadline_exceeded")
    return {"should_fallback": bool(reasons), "reasons": reasons}


def complement_audit_codes(universe_codes, coarse_without_audit_codes, cursor, cfg):
    """确定性补集审计：只从真正补集取样，环形切片精确返回 min(size, len(complement))。

    返回 (codes, next_cursor)。使用 SHA-256 稳定排序，禁止用进程内不稳定的 hash()。
    """
    import hashlib

    cfg = cfg or {}
    size = int(cfg.get("complement_audit_size", CONFIG["complement_audit_size"]))
    cursor = int(cursor or 0)
    universe = {normalize_code(code) for code in (universe_codes or [])}
    coarse = {normalize_code(code) for code in (coarse_without_audit_codes or [])}
    complement = sorted(universe - coarse)
    if not complement:
        return [], cursor
    ordered = sorted(
        complement,
        key=lambda code: hashlib.sha256(code.encode("utf-8")).hexdigest(),
    )
    take = min(size, len(ordered))
    start = cursor % len(ordered)
    codes = [ordered[(start + i) % len(ordered)] for i in range(take)]
    next_cursor = (start + take) % len(ordered)
    return codes, next_cursor

def compute_round_valid(truth, bulk_meta, audit, deadline_met, universe_verified) -> bool:
    """P0-R1：M5 单轮准入必须由完整条件计算，不能复制 truth 自带标记。"""
    if not truth or truth.get("source") != "sina_full":
        return False
    if universe_verified is not True:
        return False
    if truth.get("coverage_ratio") != 1.0:
        return False
    if truth.get("rejected_rows"):
        return False
    bulk = bulk_meta or {}
    if bulk.get("status") != "shadow_only":
        return False
    validation = bulk.get("validation") or {}
    if validation.get("valid") is not True:
        return False
    if not audit:
        return False  # 空 audit 不得冒充零漏检
    if audit.get("false_negative_buy") or audit.get("false_negative_sell"):
        return False
    if deadline_met is not True:
        return False
    return True


def compute_m5_streak(records, today) -> int:
    """M5 连续有效交易日（P0-R1）：按交易日聚合；任何无效轮次使当日无效。

    - 同一交易日内所有记录必须 valid_for_admission 且配置指纹唯一。
    - 必须覆盖上午 + 下午两个时段。
    - 使用项目交易日历跳过周末与法定休市日（周末不打断连续性）。
    """
    import datetime as _dt

    from backend.services.trading_calendar import is_trading_day, prev_trading_day

    today = _dt.date.fromisoformat(today) if isinstance(today, str) else today
    by_day = {}
    for record in records or []:
        day = record.get("trade_date")
        if not day:
            continue
        by_day.setdefault(day, []).append(record)

    def _day_valid(day_records) -> bool:
        if not day_records:
            return False
        if not all(record.get("valid_for_admission") for record in day_records):
            return False
        if len({record.get("config_fingerprint") for record in day_records}) != 1:
            return False
        sessions = {record.get("session") for record in day_records}
        if not ({"am", "pm"} <= sessions):
            return False
        return True

    streak = 0
    cursor = today
    for _ in range(3650):
        if not is_trading_day(cursor):
            prev = prev_trading_day(cursor, 1)
            if prev is None:
                break
            cursor = prev
            continue
        if not _day_valid(by_day.get(cursor.isoformat())):
            break
        streak += 1
        prev = prev_trading_day(cursor, 1)
        if prev is None:
            break
        cursor = prev
    return streak

