"""涨停基础状态与增强事件分离（§12.4 第4步）。

基础状态（zt_basic）：只用新鲜报价 + 有效涨停价，可免费获得。
增强事件（zt_enrichment）：封板/首封/炸板/连板，依赖东财 zt_pool 等完整事件源；
缺失时显式 unavailable_required_fields，不填 0、不当正常结果。

规则边界：本模块只编码有明确依据的规则版本；未知（新股/无涨跌幅限制/规则缺生效日期）返回 None。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# 规则版本：生效日期后 主板风险警示(ST) 限制比例由 5% 调整为 10%（上交所 2026 修订、深交所 2026-06-30 通知）。
# 未命中任何版本或板块不明 → unknown。
_RULE_VERSIONS = [
    {"version": "v2", "effective": date(2026, 6, 30),
     "main": 0.10, "main_st": 0.10, "gem_star": 0.20},
    {"version": "v1", "effective": date(2000, 1, 1),
     "main": 0.10, "main_st": 0.05, "gem_star": 0.20},
]

_PRICE_STEP = Decimal("0.01")


def _board(code: str):
    code = str(code).zfill(6)
    if code.startswith(("300", "301", "688")):
        return "gem_star"
    if code.startswith(("60", "000", "001", "002", "003")):
        return "main"
    return None


def _quantize_price(value: Decimal) -> Decimal:
    return value.quantize(_PRICE_STEP, rounding=ROUND_HALF_UP)


def compute_limit_prices(prev_close, code, name="", on_date=None):
    """按有效规则/参考价/报价单位计算涨停价与跌停价；未知返回 (None, None, None)。"""
    try:
        prev = Decimal(str(prev_close))
    except (InvalidOperation, ValueError, TypeError):
        return None, None, None
    if not prev.is_finite() or prev <= 0:
        return None, None, None
    board = _board(code)
    if board is None:
        return None, None, None
    st = "ST" in str(name or "").upper()
    day = on_date or date.today()
    rule = next((r for r in _RULE_VERSIONS if r["effective"] <= day), None)
    if rule is None:
        return None, None, None
    ratio = rule["main_st"] if (board == "main" and st) else (rule["gem_star"] if board == "gem_star" else rule["main"])
    up = _quantize_price(prev * (1 + Decimal(str(ratio))))
    down = _quantize_price(prev * (1 - Decimal(str(ratio))))
    return float(up), float(down), rule["version"]


def classify_zt_basic(row) -> dict:
    """基础状态：曾触及 / 当前在涨停 / 未知；不推断封板事件。"""
    code = str(row.get("代码") or row.get("code") or "").zfill(6)
    name = str(row.get("名称") or row.get("name") or "")
    price = row.get("最新价")
    high = row.get("最高价")
    limit_up = row.get("涨停价")
    unknown = []
    if not code:
        return {"code": code, "state": "unknown", "touched": False, "at_limit": False, "unknown_reasons": ["missing_code"]}
    if limit_up is None:
        limit_up, _, _ = compute_limit_prices(row.get("昨收"), code, name)
        if limit_up is None:
            unknown.append("limit_price_unknown")
    if limit_up is None:
        return {"code": code, "state": "unknown", "touched": False, "at_limit": False, "unknown_reasons": unknown}
    try:
        p = Decimal(str(price)) if price not in (None, "") else None
        h = Decimal(str(high)) if high not in (None, "") else None
        up = Decimal(str(limit_up))
    except (InvalidOperation, ValueError, TypeError):
        return {"code": code, "state": "unknown", "touched": False, "at_limit": False, "unknown_reasons": ["invalid_price"]}
    at_limit = p is not None and p == up
    touched = (h is not None and h >= up) or at_limit
    state = "at_limit" if at_limit else ("touched" if touched else "none")
    return {"code": code, "state": state, "touched": touched, "at_limit": at_limit,
            "limit_up": float(up), "unknown_reasons": unknown}


def resolve_zt_basic_from_quotes(quotes) -> "pd.DataFrame":
    """免费基础路径：从新鲜报价批量计算涨停基础状态，供消费方在不依赖东财时使用。"""
    import pandas as pd

    if quotes is None or getattr(quotes, "empty", False):
        return pd.DataFrame()
    rows = []
    for _, r in quotes.iterrows():
        state = classify_zt_basic(r.to_dict())
        rows.append({
            "代码": state["code"],
            "zt_basic_state": state["state"],
            "touched": state["touched"],
            "at_limit": state["at_limit"],
            "limit_up": state.get("limit_up"),
            "unknown_reasons": state["unknown_reasons"],
        })
    return pd.DataFrame(rows)


def enrich_zt_events(zt_pool, codes) -> dict:
    """增强事件：从东财涨停池映射封板/炸板/连板；无等价事件源时该能力 unavailable。"""
    out = {str(c).zfill(6): None for c in (codes or [])}
    if zt_pool is None or getattr(zt_pool, "empty", False):
        return out
    for _, item in zt_pool.iterrows():
        code = str(item.get("代码", "")).zfill(6)
        if code not in out:
            continue
        out[code] = {
            "seal_time": item.get("封板时间"),
            "first_seal_time": item.get("首次封板时间") if "首次封板时间" in item else None,
            "break_count": item.get("炸板次数"),
            "consecutive": item.get("连板数"),
            "available": True,
        }
    return out


def required_events_available(events: dict, required=("seal_time", "break_count")) -> list:
    """依赖精确封板字段的策略：缺数据返回 unavailable 列表，不填 0。"""
    missing = []
    for code, ev in (events or {}).items():
        if not ev or not ev.get("available"):
            missing.append(code)
            continue
        for field in required:
            if ev.get(field) in (None, "", "-"):
                missing.append(code)
                break
    return sorted(set(missing))
