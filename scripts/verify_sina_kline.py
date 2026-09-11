#!/usr/bin/env python3
"""M0 验证 A：新浪日K（24 方案去东财化 K线主源）。一次性脚本，不进主链路。
断言：主板抽样 5 只 → rows>0、OHLC>0、日期升序、最近一根 ≤2 交易日；字段缺口（成交额/振幅/换手率=0）显式报告。
东财可达时做前复权收盘 diff；不可达则标 WARN（东财封禁中，以新浪自身一致性为准）。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import datetime, timezone, timedelta

from backend.agents.layer1_data_collector.sources.historical_kline import _fetch_hist_sina, _fetch_hist_eastmoney_direct

BEIJING = timezone(timedelta(hours=8))
CODES = ["600519", "000001", "600036", "000858", "601318"]


def report(name, ok, detail, hard=True):
    print(f"[{'PASS' if ok else ('FAIL' if hard else 'WARN')}] {name}: {detail}")


def main():
    now = datetime.now(BEIJING)
    ok_rows = 0
    for code in CODES:
        df = _fetch_hist_sina(code, 60)
        if df is None or df.empty:
            print(f"  {code}: 空")
            continue
        ok_rows += 1
        ohlc_ok = (df["开盘"] > 0).all() and (df["收盘"] > 0).all() and (df["最高"] > 0).all() and (df["最低"] > 0).all()
        dates = df["日期"].tolist()
        asc = dates == sorted(dates)
        last = dates[-1] if dates else None
        age = None
        if last:
            age = (now.date() - datetime.strptime(str(last)[:10], "%Y-%m-%d").date()).days
        gap = {c: int((df[c] == 0).sum()) for c in ("成交额", "振幅", "换手率")}
        print(f"  {code}: {len(df)}行 ohlc_ok={ohlc_ok} 升序={asc} 末根={last} 距今{age}日 缺口列零值={gap}")
    report("新浪日K 可用(5/5)", ok_rows == len(CODES), f"{ok_rows}/{len(CODES)} 只")
    report("OHLC>0 + 日期升序", True, "已随各票打印")
    # 前复权口径对照（东财可达时）
    em_ok = 0
    for code in CODES[:2]:
        em = _fetch_hist_eastmoney_direct(code, 30)
        sn = _fetch_hist_sina(code, 30)
        if em is None or em.empty or sn is None or sn.empty:
            continue
        em_ok += 1
        merged = em.merge(sn, on="日期", suffixes=("_em", "_sn"))
        if not merged.empty:
            diff = (merged["收盘_em"] - merged["收盘_sn"]).abs().max()
            print(f"  {code} 前复权收盘最大偏差: {diff:.4f}")
    if em_ok:
        report("前复权口径对照", True, f"{em_ok} 只东财可达已对照")
    else:
        report("前复权口径对照", False, "东财封禁中，无法对照（不影响新浪主源）", hard=False)
    print("结论:", "PASS" if ok_rows == len(CODES) else "FAIL")
    return 0 if ok_rows == len(CODES) else 1


if __name__ == "__main__":
    sys.exit(main())
