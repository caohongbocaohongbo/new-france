"""邮件升级 · 技术指标纯函数。

输入 pd.DataFrame（中文列名：收盘/最高/最低/开盘/成交量/涨跌幅），输出标量。
全部对空/短数据返回 None，不抛异常。
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

# 中文列名 -> 英文列名（兼容 Tushare 降级源可能出现的英文列）
_COL_ALIAS = {
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "开盘": "open",
    "成交量": "volume",
    "涨跌幅": "pct_chg",
}


def _num_series(hist, col: str) -> Optional[pd.Series]:
    """取某列的数值序列；缺列/空数据返回 None。"""
    if hist is None or not isinstance(hist, pd.DataFrame) or hist.empty:
        return None
    for name in (col, _COL_ALIAS.get(col)):
        if name and name in hist.columns:
            series = pd.to_numeric(hist[name], errors="coerce").dropna()
            if not series.empty:
                return series
    return None


def atr(hist, period: int = 14) -> Optional[float]:
    """真实波幅均值（ATR，简单移动平均口径）。数据少于 period+1 行返回 None。"""
    high = _num_series(hist, "最高")
    low = _num_series(hist, "最低")
    close = _num_series(hist, "收盘")
    if high is None or low is None or close is None:
        return None
    n = min(len(high), len(low), len(close))
    if n < int(period) + 1:
        return None
    high = high.iloc[-n:].reset_index(drop=True)
    low = low.iloc[-n:].reset_index(drop=True)
    close = close.iloc[-n:].reset_index(drop=True)
    prev_close = close.shift(1)
    true_range = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    # 首个 TR 无前收，取 最高-最低
    true_range.iloc[0] = high.iloc[0] - low.iloc[0]
    value = true_range.rolling(int(period)).mean().iloc[-1]
    if pd.isna(value):
        return None
    return float(value)


def recent_low(hist, window: int = 20) -> Optional[float]:
    """近 window 日最低。空数据返回 None。"""
    series = _num_series(hist, "最低")
    if series is None or series.empty:
        return None
    return float(series.tail(int(window)).min())


def recent_high(hist, window: int = 20) -> Optional[float]:
    """近 window 日最高。空数据返回 None。"""
    series = _num_series(hist, "最高")
    if series is None or series.empty:
        return None
    return float(series.tail(int(window)).max())


def ma(hist, period: int) -> Optional[float]:
    """收盘均线。数据少于 period 行返回 None。"""
    series = _num_series(hist, "收盘")
    if series is None or len(series) < int(period):
        return None
    return float(series.tail(int(period)).mean())
