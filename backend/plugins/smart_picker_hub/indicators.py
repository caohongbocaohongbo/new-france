"""22 聚合纯函数：去重合并 / 统一分 / 门控 / 命中解释 / 图表序列（无网络、无前视）。"""
import math
from bisect import bisect_right

from backend.plugins.common import float_or

from .config import PCT_KEYS, STRATEGY_KEYS


def zcode(code) -> str:
    """6 位补零代码。"""
    return str(code or "").zfill(6)


def trading_days_after(bar_dates: list, anchor_date: str, k: int) -> str:
    """D 之后第 k 个交易日的日期字符串（22 方案 §7.4 / DIFF-6 规格）。

    直接从 K 线 bar 日期序列推导：东财 fetch_historical 返回的 bar 序列本身即交易日
    序列（已剔除休市/停牌缺 bar 日），不依赖外部日历文件、无网络依赖。
    锚点缺失或 bars 不足 → None（调用方按 data_missing 处理）。
    """
    anchor = str(anchor_date)[:10]
    try:
        idx = bar_dates.index(anchor)
    except ValueError:
        return None
    target_idx = idx + int(k)
    return bar_dates[target_idx] if 0 <= target_idx < len(bar_dates) else None


def extract_rows(snapshot) -> list:
    """单快照命中行：items 优先、各 *_pool 补齐，按 code 去重（消除 items/pool 重复行）。"""
    if not isinstance(snapshot, dict) or snapshot.get("status") != "completed":
        return []
    seen, rows = {}, []
    for key in ["items"] + [k for k in snapshot if k.endswith("_pool")]:
        for it in snapshot.get(key) or []:
            if not isinstance(it, dict):
                continue
            code = zcode(it.get("code"))
            if not code or code in seen:
                continue
            seen[code] = it
            rows.append(it)
    return rows


def fill_strategy_pct(rows, strategy) -> list:
    """缺 pct 的行用当日 score 重算百分位（旧格式快照兜底，与 18 方案 G8 同口径）。"""
    from .config import SCORE_KEYS

    pct_key, score_key = PCT_KEYS[strategy], SCORE_KEYS[strategy]
    targets = [r for r in rows if r.get(pct_key) is None]
    if not targets:
        return rows
    clean = sorted(float(s) for s in (float_or(r.get(score_key)) for r in targets) if s is not None)
    n = len(clean)
    if not n:
        return rows
    for r in targets:
        s = float_or(r.get(score_key))
        if s is not None:
            r[pct_key] = round(bisect_right(clean, s) / n * 100, 1)
    return rows


def percentiles(scores) -> list:
    """当日百分位排名 ×100（并列分取右侧，跨日可比）。"""
    clean = sorted(float(s) for s in scores if s is not None and math.isfinite(float(s)))
    n = len(clean)
    out = []
    for s in scores:
        f = float_or(s)
        out.append(round(bisect_right(clean, f) / n * 100, 1) if (f is not None and n) else None)
    return out


def _pick_name(hits) -> str:
    for k in STRATEGY_KEYS:
        if k in hits and str(hits[k].get("name") or "").strip():
            return str(hits[k].get("name")).strip()
    return None


def _pick_field(hits, key):
    """当日一致性：优先取非 intraday 行的值，否则首个非空。"""
    order = [k for k in STRATEGY_KEYS if k in hits]
    preferred = [k for k in order if not hits[k].get("is_intraday")] or order
    for k in preferred:
        v = float_or(hits[k].get(key))
        if v is not None:
            return v
    return None


def union_table(strategy_rows: dict) -> list:
    """以 code 为主键合并四策略命中行为一行。strategy_rows: {k: [row, ...]}"""
    merged = {}
    for k in STRATEGY_KEYS:
        for r in strategy_rows.get(k) or []:
            code = zcode(r.get("code"))
            if code:
                merged.setdefault(code, {}).setdefault("hits", {})[k] = r
    items = []
    for code, m in merged.items():
        hits = m["hits"]
        hit_flags = {k: 1 if k in hits else 0 for k in STRATEGY_KEYS}
        items.append({
            "code": code,
            "name": _pick_name(hits),
            "price": _pick_field(hits, "price"),
            "change_pct": _pick_field(hits, "change_pct"),
            "total_amount": _pick_field(hits, "total_amount"),
            "is_intraday": 1 if any(hits[k].get("is_intraday") for k in hits) else 0,
            "hit_strategies": sum(hit_flags.values()),
            "hit_flags": hit_flags,
            "pct": {k: float_or(hits[k].get(PCT_KEYS[k])) for k in hits},
            "hits": hits,
        })
    return items


def normalize_weights(weights: dict, available: dict) -> dict:
    """剔除不可用策略并归一化；全不可用返回空 dict。"""
    w = {k: float(v) for k, v in (weights or {}).items() if available.get(k) and float(v) > 0}
    total = sum(w.values())
    return {k: round(v / total, 6) for k, v in w.items()} if total > 0 else {}


def compute_hub_score(pcts: dict, weights: dict) -> float:
    """统一分 = 可用策略 pct 加权平均（0-100）。"""
    wsum = total = 0.0
    for k, p in (pcts or {}).items():
        w = float(weights.get(k, 0) or 0)
        if w > 0 and p is not None:
            total += w * p
            wsum += w
    return round(total / wsum, 2) if wsum > 0 else 0.0


def apply_gates(items: list, zt_codes=None, cfg: dict = None) -> tuple:
    """门控：涨停剔除 + 市场过滤（复用 common.market_filter 口径）+ 成交额下限。

    返回 (items, zt_applied, zt_reason)；zt_codes=None 表示 zt_pool 不可用 → 跳过涨停门控。
    """
    cfg = cfg or {}
    if not items:
        return [], zt_codes is not None, (None if zt_codes is not None else "zt_pool_unavailable")
    import pandas as pd

    from backend.plugins.common import market_filter

    zt_applied = zt_codes is not None
    zt_reason = None if zt_applied else "zt_pool_unavailable"
    zt_set = {zcode(c) for c in (zt_codes or [])}
    df = pd.DataFrame([{
        "code": it.get("code"), "name": it.get("name") or "",
        "total_amount": float_or(it.get("total_amount")),
    } for it in items])
    min_amount = float(cfg.get("min_amount") or 0)
    filtered = market_filter(
        df, show_gem=bool(cfg.get("show_gem")), show_star=bool(cfg.get("show_star")),
        min_amount=min_amount if min_amount > 0 else None,
    )
    kept = {zcode(c) for c in filtered["code"].tolist()}
    out = [it for it in items if zcode(it.get("code")) in kept and zcode(it.get("code")) not in zt_set]
    return out, zt_applied, zt_reason


def _market_ok(code: str, market: str) -> bool:
    """按 market 参数过滤板块：main 仅主板；gem 加创业板；star 加科创板。"""
    code = zcode(code)
    if market == "gem":
        return not code.startswith("688")
    if market == "star":
        return not (code.startswith("300") or code.startswith("301"))
    return not code.startswith(("300", "301", "688"))


def _sort_value(item: dict, key: str):
    if key == "code":
        return item.get("code")
    hits = item.get("hits") or {}
    for k in STRATEGY_KEYS:
        hit = hits.get(k)
        if hit and hit.get(key) is not None:
            return float_or(hit.get(key))
    return float_or(item.get(key))


def filter_items(items: list, q: str = "", pool: str = "all", min_hit: int = 1,
                 market: str = "main", sort: str = "hub_score", order: str = "desc",
                 limit: int = 50, offset: int = 0) -> dict:
    """服务端查询：q 搜索 / pool / min_hit / market 过滤 + 排序 + 分页。

    返回 {"all": 过滤后全量, "page": 当前页}；tie-break 固定 hub_score→hit_strategies→code。
    """
    q = (q or "").strip().lower()
    out = []
    for it in items or []:
        if not _market_ok(it.get("code"), market):
            continue
        if str(it.get("name") or "").upper().find("ST") >= 0:
            continue
        if int(it.get("hit_strategies") or 0) < int(min_hit):
            continue
        if pool == "resonance" and not it.get("resonance"):
            continue
        if pool in STRATEGY_KEYS and not (it.get("hit_flags") or {}).get(pool):
            continue
        if q:
            if not (zcode(it.get("code")).startswith(q) or q in str(it.get("name") or "").lower()):
                continue
        out.append(it)
    desc = order != "asc"
    sign = -1 if desc else 1

    def sort_key(r):
        v = _sort_value(r, sort)
        if sort == "code":
            v = int(r.get("code") or 0) if str(r.get("code") or "").isdigit() else None
        # None 分：升序排最前（最小），降序排最后；tie-break 固定 hub_score→hit_strategies→code（保证翻页稳定）
        primary = (math.inf if desc else -math.inf) if v is None else sign * v
        return (primary, sign * (float_or(r.get("hub_score")) or 0),
                sign * int(r.get("hit_strategies") or 0), sign * int(r.get("code") or 0))

    out.sort(key=sort_key)
    page = out[int(offset): int(offset) + int(limit)]
    return {"all": out, "page": page}


def _f(v, nd: int = 2):
    """格式化浮点（None → --）。"""
    f = float_or(v)
    return "--" if f is None else f"{f:.{nd}f}"


def explain_tech(r: dict) -> str:
    parts = []
    if r.get("macd_golden"):
        parts.append(f"MACD金叉(DIF {_f(r.get('macd_dif'))} 上穿 DEA {_f(r.get('macd_dea'))})")
    if r.get("kdj_golden"):
        parts.append(f"KDJ低位金叉(K {_f(r.get('kdj_k'))} 上穿 D {_f(r.get('kdj_d'))})")
    if r.get("rsi_oversold"):
        parts.append(f"RSI超卖反弹(RSI {_f(r.get('rsi'))}<30 回升)")
    if r.get("boll_rebound"):
        parts.append(f"BOLL下轨反弹(今收高于昨收且站上5日均线, 中轨 {_f(r.get('boll_mb'))})")
    return " + ".join(parts) or None


def explain_trend(r: dict) -> str:
    parts = []
    if r.get("ma_aligned"):
        parts.append(f"MA5/10/20/60多头排列({_f(r.get('ma5'))}>{_f(r.get('ma10'))}>{_f(r.get('ma20'))}>{_f(r.get('ma60'))})")
    if r.get("new_high") and r.get("prev_high_60") is not None:
        parts.append(f"创60日新高(前高 {_f(r.get('prev_high_60'))}, 突破 {_f((r.get('high_break_pct') or 0) * 100)}%)")
    return " + ".join(parts) or None


def explain_pattern(r: dict) -> str:
    parts = []
    if r.get("platform_break"):
        parts.append(f"窄幅平台放量突破(平台上沿 {_f(r.get('platform_high'))}, 量比 {_f(r.get('volume_ratio'))})")
    if r.get("gap_hold"):
        parts.append(f"{'突破' if r.get('gap_type') == 'breakaway' else '普通'}缺口不回补(缺口 {_f((r.get('gap_pct') or 0) * 100)}%, 下沿 {_f(r.get('gap_low'))})")
    return " + ".join(parts) or None


def explain_chip(r: dict) -> str:
    parts = []
    if r.get("concentrated"):
        parts.append(f"筹码集中(90%成本区间 {_f(r.get('concentration_ratio'))})")
    if r.get("high_profit"):
        parts.append(f"获利盘 {_f((r.get('profit_ratio') or 0) * 100)}%")
    if r.get("trend_up"):
        parts.append(f"MA20上行(斜率 {_f(r.get('ma20_slope'), 6)})")
    return " + ".join(parts) or None


def build_chart_series(records: list) -> dict:
    """由日线 records 计算全序列（复用 18 的 *_series_full，暖机期 None，供 ECharts 直接绘制）。"""
    from backend.plugins.tech_indicators.indicators import (
        boll_series_full, kdj_series_full, macd_series_full, ma_series_full, rsi_series_full,
    )

    closes = [r.get("close") for r in records]
    highs = [r.get("high") for r in records]
    lows = [r.get("low") for r in records]
    return {
        "ma": ma_series_full(closes) or {},
        "macd": macd_series_full(closes) or {},
        "kdj": kdj_series_full(closes, highs, lows) or {},
        "rsi": rsi_series_full(closes) or [],
        "boll": boll_series_full(closes) or {},
    }
