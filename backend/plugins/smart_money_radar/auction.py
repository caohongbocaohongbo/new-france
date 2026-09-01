"""13 集合竞价异动监测（纯函数）。"""
import math


def _float(value, default=None):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def auction_gap(quote: dict):
    """竞价高开幅度 = (虚拟开盘价 - 昨收) / 昨收。昨收=0 防除零。"""
    price = _float(quote.get("price"))
    last_close = _float(quote.get("last_close"))
    if price is None or last_close is None or last_close == 0:
        return None
    return round((price - last_close) / last_close, 4)


def auction_amount(quote: dict):
    """竞价匹配金额 = 虚拟开盘价 × 匹配量 × 100。"""
    price = _float(quote.get("price"), 0) or 0
    vol = _float(quote.get("vol"), 0) or 0
    return round(price * vol * 100, 2)


def match_vol_slope(series: list) -> float:
    """匹配量增速 = 最近 K 帧匹配量斜率（正 = 持续承接），归一化。"""
    vals = [_float(v, 0) or 0 for v in series or []]
    if len(vals) < 2:
        return 0.0
    xs = list(range(len(vals)))
    mean_x = sum(xs) / len(xs); mean_y = sum(vals) / len(vals)
    denom = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, vals)) / denom if denom else 0.0
    return round(slope / mean_y, 4) if mean_y else 0.0


def withdraw_lure(vol_series: list, price_series: list, drop_pct: float = 0.5) -> bool:
    """撤单诱多：挂单量骤减(>=drop_pct)且开盘价下移。"""
    vols = [_float(v, 0) or 0 for v in vol_series or []]
    prices = [_float(p) for p in price_series or []]
    if len(vols) < 2 or vols[0] <= 0:
        return False
    vol_drop = (vols[0] - vols[-1]) / vols[0] >= drop_pct
    price_down = len(prices) >= 2 and prices[0] is not None and prices[-1] is not None and prices[-1] < prices[0]
    return vol_drop and price_down


def is_in_auction_window(now, cfg: dict = None) -> bool:
    """是否处于早盘集合竞价时窗 09:15-09:25（尾盘 14:57-15:00 另行处理）。"""
    hhmm = now.strftime("%H:%M")
    cfg = cfg or {}
    start = cfg.get("auction_start", "09:15")
    end = cfg.get("auction_end", "09:25")
    return start <= hhmm <= end


def is_in_closing_auction(now) -> bool:
    """是否处于尾盘集合竞价 14:57-15:00。"""
    return "14:57" <= now.strftime("%H:%M") <= "15:00"


def prev_zt_auction_strength(store, zt_codes, now) -> dict:
    """昨日涨停股今日竞价高开幅度分布（与 01 情绪周期联动，01 未落地时静默跳过）。"""
    if not zt_codes:
        return {"available": False, "count": 0, "avg_gap": None, "items": []}
    gaps = []
    for code, frames in (getattr(store, "auction_frames", {}) or {}).items():
        if code not in zt_codes or not frames:
            continue
        gap = auction_gap((frames[-1].get("quote") or {}))
        if gap is not None:
            gaps.append({"code": code, "gap": gap})
    if not gaps:
        return {"available": True, "count": 0, "avg_gap": None, "items": []}
    avg = sum(g["gap"] for g in gaps) / len(gaps)
    return {"available": True, "count": len(gaps), "avg_gap": round(avg, 4), "items": gaps}


def build_auction_snapshot(store, now, zt_codes=None) -> dict:
    """由内存 auction_frames 汇总竞价榜（含昨日涨停竞价强度联动）。"""
    items = []
    for code, frames in (getattr(store, "auction_frames", {}) or {}).items():
        if not frames:
            continue
        name = (frames[-1].get("quote") or {}).get("name") or ""
        items.append(evaluate_auction(code, name, list(frames)))
    items.sort(key=lambda r: (r.get("gap") if r.get("gap") is not None else -1e9), reverse=True)
    prev_zt = prev_zt_auction_strength(store, zt_codes or set(), now)
    return {
        "status": "completed" if items else "no_data",
        "now": now.isoformat(),
        "count": len(items),
        "zt_data_available": prev_zt["available"],
        "prev_zt_auction": prev_zt,
        "items": items,
    }


def load_prev_zt_codes(now) -> set:
    """加载昨日涨停代码（01 方案 limit_up_daily 表）；01 未落地返回空集，静默跳过。"""
    try:
        from backend.plugins.common import db_query
        from backend.services.trading_calendar import prev_trading_day

        prev = prev_trading_day(now.date())
        if prev is None:
            return set()
        df = db_query("SELECT DISTINCT code FROM limit_up_daily WHERE date = :d", {"d": prev.isoformat()})
        if df.empty:
            return set()
        return {str(r.code).zfill(6) for r in df.itertuples()}
    except Exception:  # noqa: BLE001 01 未落地/DB 失败静默降级，不报错
        return set()


def evaluate_auction(code: str, name: str, frames: list) -> dict:
    """由竞价采样帧序列计算指标与信号。frames: [{quote}]。"""
    if not frames:
        return {"code": str(code).zfill(6), "name": name, "gap": None, "amount": None, "events": []}
    latest = frames[-1].get("quote") or {}
    vols = [_float((f.get("quote") or {}).get("vol"), 0) or 0 for f in frames]
    prices = [_float((f.get("quote") or {}).get("price")) for f in frames]
    total_order = [_float(sum(_float((f.get("quote") or {}).get(f"bid_vol{i}"), 0) or 0 for i in range(1, 6)) + sum(_float((f.get("quote") or {}).get(f"ask_vol{i}"), 0) or 0 for i in range(1, 6))) for f in frames]
    events = []
    if withdraw_lure(total_order, prices):
        events.append("疑似诱多撤单")
    slope = match_vol_slope(vols)
    if slope > 0.2:
        events.append("竞价持续承接")
    return {
        "code": str(code).zfill(6), "name": name,
        "gap": auction_gap(latest),
        "amount": auction_amount(latest),
        "match_vol_slope": slope,
        "events": events,
    }
