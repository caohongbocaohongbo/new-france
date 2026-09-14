"""源验证共用硬断言：FAIL退出1，证据未完成退出2，全部通过才退出0。"""
from __future__ import annotations

import pandas as pd


class Checks:
    def __init__(self):
        self.failed = []
        self.pending = []

    def check(self, label, ok, detail=""):
        if not ok:
            self.failed.append(label)
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: {detail}")
        return bool(ok)

    def skip(self, label, detail):
        self.pending.append(label)
        print(f"[未完成] {label}: {detail}")

    def finish(self):
        code = 1 if self.failed else (2 if self.pending else 0)
        print(f"结论: {'FAIL' if code == 1 else '未完成' if code == 2 else 'PASS'}；"
              f"失败{len(self.failed)}项，未完成{len(self.pending)}项")
        return code


def validate_bars(df):
    """验证每行OHLCV、日线身份、唯一性与升序；不证明复权类型。"""
    if df is None or df.empty:
        return False
    required = ["日期", "开盘", "收盘", "最高", "最低", "成交量"]
    if any(key not in df.columns for key in required):
        return False
    dates = pd.to_datetime(df["日期"], errors="coerce")
    values = df[required[1:]].apply(pd.to_numeric, errors="coerce")
    import numpy as np
    return bool(dates.notna().all() and dates.is_unique and dates.is_monotonic_increasing
                and np.isfinite(values.to_numpy()).all()
                and (values[["开盘", "收盘", "最高", "最低"]] > 0).all().all()
                and (values["成交量"] >= 0).all()
                and (values["最高"] >= values[["开盘", "收盘", "最低"]].max(axis=1)).all()
                and (values["最低"] <= values[["开盘", "收盘", "最高"]].min(axis=1)).all())


def compare_closes(left, right, min_rows=20, tolerance_pct=0.1):
    """按日期对齐，逐日相对误差；不允许空交集或zip截断假通过。"""
    if not validate_bars(left) or not validate_bars(right):
        return False, "K线结构校验失败"
    a, b = left.copy(), right.copy()
    a["日期"] = pd.to_datetime(a["日期"]).dt.strftime("%Y-%m-%d")
    b["日期"] = pd.to_datetime(b["日期"]).dt.strftime("%Y-%m-%d")
    matched = a.merge(b, on="日期", suffixes=("_a", "_b"), validate="one_to_one")
    if len(matched) < min_rows:
        return False, f"共同交易日不足: {len(matched)}/{min_rows}"
    error = ((matched["收盘_a"] - matched["收盘_b"]).abs() / matched["收盘_b"].abs() * 100).max()
    return bool(error <= tolerance_pct), f"共同{len(matched)}日，最大逐日偏差{error:.4f}%"


def check_recency(checks, df, now):
    """使用项目日历；日历降级不伪装完成交易日新鲜度验收。"""
    from backend.services.trading_calendar import calendar_status, prev_trading_day
    if df is None or df.empty:
        checks.check("最新交易日", False, "空K线")
        return
    if calendar_status().get("degraded"):
        checks.skip("最新交易日", "权威交易日历不可用")
        return
    previous = prev_trading_day(now.date())
    if previous is None:
        checks.skip("最新交易日", "日历范围不覆盖当前日期")
        return
    last = pd.to_datetime(df["日期"]).max().date()
    checks.check("最新交易日", previous <= last <= now.date(), str(last))
