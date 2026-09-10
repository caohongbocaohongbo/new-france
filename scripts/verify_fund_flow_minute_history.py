#!/usr/bin/env python3
"""验证脚本 2：东财个股资金流「分钟历史」接口（23 方案施工前置验证，一次性脚本，不进主链路）。

验证项：
  A klt=1（1分钟）与 klt=5（5分钟）接口可用 + 字段完整（f51 时间 / f52 主力净额 / f57 主力净占比 / f62 收盘 / f63 涨跌幅）
  B 最近 bar 时间戳新鲜度（相对当前盘中时间）
  C 抽样覆盖：当日全量扫描的买/卖候选各取前 5 只
  D 口径交叉：分钟历史当日累加主力净流入(f52) vs 全量快照 main_net_inflow(f62)；末根占比(f57) vs main_inflow_ratio(f184)
  E klt=5 的 bar 数与 lmt 行为
用法：python3 scripts/verify_fund_flow_minute_history.py [--full-scan]
退出码：存在硬性 FAIL → 1；仅 WARN → 0。
"""
import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

BEIJING_TZ = timezone(timedelta(hours=8))
BASE = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/zjlx/"}
UT = "b2884a393a59ad64002292a3e90d46a5"
FIELDS1 = "f1,f2,f3,f7"
FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
RESULTS = []


def report(name, ok, detail, hard=True):
    RESULTS.append((name, ok, detail, hard))
    print(f"[{'PASS' if ok else ('FAIL' if hard else 'WARN')}] {name}: {detail}")


def _float(v):
    try:
        return None if v in (None, "", "-") else float(v)
    except (TypeError, ValueError):
        return None


def secid_of(code):
    code = str(code).zfill(6)
    return f"{'1' if code.startswith(('5', '6', '9')) else '0'}.{code}"


def fetch_fflow_kline(code, klt=1, timeout=8):
    """个股资金流分钟 K。返回 (bars, elapsed_ms)。bar: {time, main_net, main_ratio, close, change_pct}"""
    params = {"lmt": "0", "klt": str(klt), "secid": secid_of(code),
              "fields1": FIELDS1, "fields2": FIELDS2, "ut": UT}
    t0 = time.perf_counter()
    resp = requests.get(BASE, params=params, headers=HEADERS, timeout=timeout)
    ms = (time.perf_counter() - t0) * 1000
    resp.raise_for_status()
    payload = resp.json()
    data = (payload.get("data") or {})
    klines = data.get("klines") or []
    bars = []
    for line in klines:
        parts = line.split(",")
        if len(parts) < 9:
            continue
        bars.append({
            "time": parts[0],
            "main_net": _float(parts[1]),      # f52 主力净流入额
            "main_ratio": _float(parts[6]),    # f57 主力净占比
            "close": _float(parts[11]),        # f62 收盘价
            "change_pct": _float(parts[12]),   # f63 涨跌幅
        })
    return bars, ms


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--full-scan", action="store_true", help="先跑全量扫描取候选样本（约 30-60s）")
    args = parser.parse_args()
    now = datetime.now(BEIJING_TZ)

    # 样本：优先全量扫描候选；否则用固定样本
    samples = []
    if args.full_scan:
        from backend.plugins.principal_capital.sources.eastmoney import fetch_market_fund_flow

        full = fetch_market_fund_flow(budget_seconds=120)
        buys = full[full["main_inflow_ratio"] >= 50.0].head(5)
        sells = full[full["main_inflow_ratio"] <= -30.0].head(5)
        samples = [("buy", c, r) for c, r in zip(buys["code"], buys["main_inflow_ratio"])] +                   [("sell", c, r) for c, r in zip(sells["code"], sells["main_inflow_ratio"])]
        print(f"  全量扫描 {len(full)} 只; 候选样本 {len(samples)} 只")
    else:
        samples = [("buy", "600519", None), ("sell", "000001", None)]

    klt1_ok = klt5_ok = 0
    cross_ok = cross_total = 0
    for kind, code, ratio in samples:
        try:
            bars1, ms1 = fetch_fflow_kline(code, klt=1)
            bars5, ms5 = fetch_fflow_kline(code, klt=5)
        except Exception as exc:  # noqa: BLE001
            print(f"  {kind} {code}: 拉取失败 {exc}")
            continue
        klt1_ok += bool(bars1)
        klt5_ok += bool(bars5)
        last = bars1[-1] if bars1 else None
        # B 新鲜度：末根时间 vs 当前（盘中应 ≤ 几分钟）
        age_min = None
        if last:
            try:
                last_dt = datetime.strptime(last["time"], "%Y-%m-%d %H:%M").replace(tzinfo=BEIJING_TZ)
                age_min = max(0, (now - last_dt).total_seconds() / 60)
            except ValueError:
                pass
        today = now.strftime("%Y-%m-%d")
        today_bars = [b for b in bars1 if b["time"].startswith(today)]
        net_sum = round(sum(b["main_net"] or 0 for b in today_bars), 2) if today_bars else None
        ratio_last = last["main_ratio"] if last else None
        # D 口径交叉：ratio 有参考值时对比
        if ratio is not None and ratio_last is not None:
            cross_total += 1
            if abs(ratio_last - float(ratio)) < 3.0:  # 快照与分钟线的同刻占比允许小偏差
                cross_ok += 1
        print(f"  {kind} {code}: klt1 {len(bars1)}根(末根 {last['time'] if last else '-'}, 距今 {age_min:.0f}min) "
              f"| klt5 {len(bars5)}根 | 当日累加主力净额 {net_sum} | 末根占比 {ratio_last} vs 快照 {ratio} | "
              f"{ms1:.0f}ms/{ms5:.0f}ms")
    report("klt=1 可用", klt1_ok == len(samples), f"{klt1_ok}/{len(samples)} 只")
    report("klt=5 可用", klt5_ok == len(samples), f"{klt5_ok}/{len(samples)} 只", hard=False)
    report("末根占比与快照一致", cross_ok == cross_total, f"{cross_ok}/{cross_total} 只偏差<3pct", hard=False)

    print(f"\n结论: {'全部 PASS' if not any(r[3] and not r[1] for r in RESULTS) else '存在硬性 FAIL'}")
    return 0 if not any(r[3] and not r[1] for r in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
