"""邮件升级 · 策略胜率回测（滚动模拟，零持久化）。

用历史 K 线现算，可复现，不依赖历史推荐落库。
严禁前视偏差：识别触发点只用触发日当天及之前的数据，收益用触发日之后 T+N 统计。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

BEIJING_TZ = timezone(timedelta(hours=8))

# 涨停识别区间（涨跌幅 %）：主板≈10%（含四舍五入容差），双创≈20%。
_LIMIT_UP_RANGES = ((9.5, 10.5), (19.5, 20.5))

# 当日结果缓存（模块级 dict，key=target_date），避免重复计算。
_CACHE: dict = {}


def _num_series(hist, col: str) -> Optional[pd.Series]:
    alias = {"收盘": "close", "涨跌幅": "pct_chg", "日期": "date"}
    if hist is None or not isinstance(hist, pd.DataFrame) or hist.empty:
        return None
    for name in (col, alias.get(col)):
        if name and name in hist.columns:
            series = pd.to_numeric(hist[name], errors="coerce")
            if not series.empty:
                return series.reset_index(drop=True)
    return None


def _is_limit_up(value: float) -> bool:
    if value is None or pd.isna(value):
        return False
    return any(lo <= value <= hi for lo, hi in _LIMIT_UP_RANGES)


def run_rolling_backtest(historical: dict, strategy_cfg: dict,
                         hold_days_list=(1, 3, 5)) -> dict:
    """滚动回测：识别'涨停后回撤3-10%'触发点，统计 T+1/T+3/T+5 收益。"""
    target_date = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    if target_date in _CACHE:
        return _CACHE[target_date]

    cfg = strategy_cfg or {}
    drop_min = float(cfg.get("dropMin", 3.0))
    drop_max = float(cfg.get("dropMax", 10.0))
    holds = [int(d) for d in (hold_days_list or (1, 3, 5)) if int(d) > 0]
    if not holds:
        holds = [1, 3, 5]

    samples: list = []  # 每个样本: {"returns": {hold: ret|None}, "max_dd": float}
    for hist in (historical or {}).values():
        if hist is None or getattr(hist, "empty", True):
            continue
        closes = _num_series(hist, "收盘")
        if closes is None or len(closes) < 5:
            continue
        changes = _num_series(hist, "涨跌幅")
        if changes is None or len(changes) != len(closes):
            changes = closes.pct_change() * 100
        changes = changes.reset_index(drop=True)
        closes = closes.reset_index(drop=True)

        n = len(closes)
        for i in range(n - 1):
            if not _is_limit_up(changes.iloc[i]):
                continue
            ref = float(closes.iloc[i])
            if ref <= 0:
                continue
            # 涨停次日起，找首个落入 3-10% 回撤区间的触发点
            for j in range(i + 1, n):
                drawdown = (float(closes.iloc[j]) - ref) / ref * 100
                if drawdown <= -drop_max:
                    break  # 跌破下限，本涨停日无有效触发
                if drawdown <= -drop_min:
                    entry = float(closes.iloc[j])
                    if entry <= 0:
                        break
                    max_hold = max(holds)
                    end = min(j + max_hold, n - 1)
                    window = closes.iloc[j:end + 1]
                    max_dd = round((float(window.min()) - entry) / entry * 100, 2)
                    rets = {}
                    for h in holds:
                        k = j + h
                        rets[h] = round((float(closes.iloc[k]) - entry) / entry * 100, 2) if k < n else None
                    samples.append({"returns": rets, "max_dd": max_dd})
                    break

    sample_count = len(samples)
    win_rate: dict = {}
    avg_return: dict = {}
    for h in holds:
        vals = [s["returns"][h] for s in samples if s["returns"].get(h) is not None]
        if vals:
            win_rate[h] = round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1)
            avg_return[h] = round(sum(vals) / len(vals), 2)
        else:
            win_rate[h] = 0.0
            avg_return[h] = 0.0

    avg_max_drawdown = round(sum(s["max_dd"] for s in samples) / sample_count, 2) if sample_count else 0.0
    # 纯 K 线回测无评分信号，无法区分高分档（STRONG_BUY），样本恒为空 → None。
    strong_buy_win_rate = None

    disclaimer = "基于历史K线样本统计，非未来收益承诺"
    if sample_count < 20:
        disclaimer += "（样本偏少，仅供参考）"

    result = {
        "sample_count": sample_count,
        "win_rate": win_rate,
        "avg_return": avg_return,
        "avg_max_drawdown": avg_max_drawdown,
        "strong_buy_win_rate": strong_buy_win_rate,
        "generated_at": target_date,
        "disclaimer": disclaimer,
    }
    _CACHE[target_date] = result
    return result
