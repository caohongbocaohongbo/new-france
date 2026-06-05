"""回撤累计计算工具。"""
from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd


def normalize_trade_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) >= 10 and "-" in text:
        return text[:10]
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return text[:10]


def safe_price(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number <= 0:
        return None
    return number


def _safe_pct(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:
        return 0.0
    return number


def _column(df: pd.DataFrame, *names: str) -> Optional[str]:
    for name in names:
        if name in df.columns:
            return name
    return None


def cumulative_drawdown_values(rows: List[Dict[str, Any]], watch_date: str, ref_price: float) -> List[Optional[float]]:
    """从首次关注日开始，计算每日收盘价相对参考价的回撤。"""
    if ref_price <= 0:
        return [None for _ in rows]

    start = normalize_trade_date(watch_date)
    values: List[Optional[float]] = []
    for row in rows:
        day = normalize_trade_date(row.get("date"))
        close = safe_price(row.get("close"))
        if not day or close is None or (start and day < start):
            values.append(None)
            continue
        values.append(round((close - ref_price) / ref_price * 100, 2))
    return values


def build_cumulative_price_history_series(
    hist: Optional[pd.DataFrame],
    watch_date: str,
    ref_price: float,
    current_price: Optional[float] = None,
    current_date: Optional[date] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """生成从关注日起的收盘价、涨跌幅和参考价回撤序列。"""
    if hist is None or hist.empty or ref_price <= 0:
        return []

    date_col = _column(hist, "日期", "date")
    close_col = _column(hist, "收盘", "close")
    pct_col = _column(hist, "涨跌幅", "pctChg", "pct_chg")
    if date_col is None or close_col is None:
        return []

    start = normalize_trade_date(watch_date)
    rows: List[Dict[str, Any]] = []
    for _, row in hist.iterrows():
        day = normalize_trade_date(row.get(date_col))
        if not day or (start and day < start):
            continue
        close = safe_price(row.get(close_col))
        if close is None:
            continue
        rows.append({
            "date": day,
            "close": round(close, 2),
            "change_pct": round(_safe_pct(row.get(pct_col)) if pct_col else 0.0, 2),
        })

    live_price = safe_price(current_price)
    if live_price is not None and current_date is not None:
        today_str = current_date.strftime("%Y-%m-%d")
        if not rows or rows[-1]["date"] < today_str:
            rows.append({
                "date": today_str,
                "close": round(live_price, 2),
                "change_pct": 0.0,
            })

    drawdowns = cumulative_drawdown_values(rows, start, ref_price)
    for row, drawdown in zip(rows, drawdowns):
        row["drawdown_pct"] = drawdown

    return rows[-limit:] if limit else rows


def latest_cumulative_drawdown(
    hist: Optional[pd.DataFrame],
    watch_date: str,
    ref_price: float,
    current_price: Optional[float] = None,
    current_date: Optional[date] = None,
) -> Optional[float]:
    rows = build_cumulative_price_history_series(
        hist,
        watch_date,
        ref_price,
        current_price=current_price,
        current_date=current_date,
    )
    for row in reversed(rows):
        value = row.get("drawdown_pct")
        if value is not None:
            return float(value)
    live_price = safe_price(current_price)
    if live_price is None or ref_price <= 0:
        return None
    return round((live_price - ref_price) / ref_price * 100, 2)
