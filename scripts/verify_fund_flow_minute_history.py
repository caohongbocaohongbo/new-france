#!/usr/bin/env python3
"""验证脚本 2：东财个股资金流「分钟历史」接口（23 方案 M0 施工前置，一次性脚本，不进主链路）。

量化门槛（grill P1 收紧，2026-09-10）：
  A klt=1 / klt=5 可达 + 字段完整（f51 时间 / f52 主力净额 / f57 主力净占比 / f62 收盘 / f63 涨跌幅）
  B 新鲜度【硬门槛】：交易时段内（9:30-11:30 / 13:00-15:00 工作日）最新 bar 时间戳距当前 ≤ 10 分钟；
    集合竞价（9:15-9:25）与午休（11:30-13:00）期间该项只提示不判 FAIL（bar 时效性有本质差异）
  C 口径交叉【硬门槛】：当日累加 f52 与同时刻 ssi_ssfx_flzjtj 计算 main_net 相对偏差 ≤ 5%
  D 抽样覆盖：新浪 bulk 买/卖头部各 3 只（1 请求取全市场，避免 3195 次全量扫描）
用法：python3 scripts/verify_fund_flow_minute_history.py [--sample-n 3] [--fresh-min 10] [--ratio-tol 0.05]
退出码：存在硬性 FAIL → 1。
"""
import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

BEIJING_TZ = timezone(timedelta(hours=8))
FFLOW_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
BULK_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "MoneyFlow.ssl_bkzj_ssggzj")
SINGLE_URL = ("http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
              "MoneyFlow.ssi_ssfx_flzjtj")
H = {"User-Agent": "Mozilla/5.0"}
H_EM = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/zjlx/"}
H_SINA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
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


def fetch_fflow(code, klt=5, timeout=8):
    params = {"lmt": "0", "klt": str(klt), "secid": secid_of(code),
              "fields1": FIELDS1, "fields2": FIELDS2, "ut": UT}
    t0 = time.perf_counter()
    resp = requests.get(FFLOW_URL, params=params, headers=H_EM, timeout=timeout)
    ms = (time.perf_counter() - t0) * 1000
    resp.raise_for_status()
    klines = (resp.json().get("data") or {}).get("klines") or []
    bars = []
    for line in klines:
        parts = line.split(",")
        if len(parts) < 13:
            continue
        bars.append({"time": parts[0], "main_net": _float(parts[1]), "main_ratio": _float(parts[6]),
                     "close": _float(parts[11]), "change_pct": _float(parts[12])})
    return bars, ms


def fetch_sina_main(code):
    prefix = "sh" if str(code).zfill(6).startswith(("5", "6", "9")) else "sz"
    resp = requests.get(SINGLE_URL, params={"daima": prefix + str(code).zfill(6)},
                        headers=H_SINA, timeout=8)
    resp.raise_for_status()
    data = resp.json()
    item = data[0] if isinstance(data, list) else data
    r0i, r0o = _float(item.get("r0_in")), _float(item.get("r0_out"))
    r1i, r1o = _float(item.get("r1_in")), _float(item.get("r1_out"))
    return (r0i - r0o) + (r1i - r1o)


def is_trading_hours(now):
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return (9 * 60 + 30 <= hm <= 11 * 60 + 30) or (13 * 60 <= hm <= 15 * 60)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-n", type=int, default=3)
    parser.add_argument("--fresh-min", type=int, default=10)
    parser.add_argument("--ratio-tol", type=float, default=0.05)
    args = parser.parse_args()
    now = datetime.now(BEIJING_TZ)
    in_session = is_trading_hours(now)
    print(f"当前 {now:%Y-%m-%d %H:%M} 北京；交易时段={'是' if in_session else '否（新鲜度为提示项）'}")

    # 抽样：新浪 bulk 买/卖头部各 N（1 请求全市场）
    samples = []
    try:
        rows = requests.get(BULK_URL, params={"num": "8000", "sort": "r0_net", "asc": "0"},
                            headers=H_SINA, timeout=20).json()
        buys = [str(r.get("symbol"))[2:].zfill(6) for r in rows
                if str(r.get("symbol") or "").startswith(("sh6", "sz0", "sz3"))][: args.sample_n]
        rows_asc = requests.get(BULK_URL, params={"num": "8000", "sort": "r0_net", "asc": "1"},
                                headers=H_SINA, timeout=20).json()
        sells = [str(r.get("symbol"))[2:].zfill(6) for r in rows_asc
                 if str(r.get("symbol") or "").startswith(("sh6", "sz0", "sz3"))][: args.sample_n]
        samples = [("buy", c) for c in buys] + [("sell", c) for c in sells]
    except Exception as exc:  # noqa: BLE001
        print(f"bulk 抽样失败，用固定样本: {exc}")
        samples = [("buy", "600519"), ("sell", "000001")]
    print(f"样本 {len(samples)} 只: {[c for _, c in samples]}")

    klt1_ok = klt5_ok = fresh_ok = cross_ok = checked = 0
    fresh_checked = 0
    for kind, code in samples:
        # A：klt=5（主）+ klt=1（辅）
        try:
            bars5, ms5 = fetch_fflow(code, klt=5)
        except Exception as exc:  # noqa: BLE001
            print(f"  {kind} {code}: fflow klt=5 拉取失败 {exc}")
            continue
        try:
            bars1, _ = fetch_fflow(code, klt=1)
        except Exception:
            bars1 = []
        klt5_ok += bool(bars5)
        klt1_ok += bool(bars1)
        last = bars5[-1] if bars5 else None
        # B：新鲜度（交易时段内硬门槛 ≤ fresh_min 分钟）
        age_min = None
        if last:
            try:
                last_dt = datetime.strptime(last["time"], "%Y-%m-%d %H:%M").replace(tzinfo=BEIJING_TZ)
                age_min = max(0, (now - last_dt).total_seconds() / 60)
            except ValueError:
                pass
        if in_session and age_min is not None:
            fresh_checked += 1
            if age_min <= args.fresh_min:
                fresh_ok += 1
        # C：口径交叉（当日 f52 累加 vs 新浪逐票 main_net，偏差 ≤ ratio_tol）
        today = now.strftime("%Y-%m-%d")
        today_bars = [b for b in bars5 if b["time"].startswith(today)]
        f52_sum = sum(b["main_net"] or 0 for b in today_bars) if today_bars else None
        try:
            sina_main = fetch_sina_main(code)
        except Exception as exc:  # noqa: BLE001
            sina_main = None
            print(f"  {kind} {code}: 新浪逐票对照失败 {exc}")
        cross_note = "skip"
        if f52_sum is not None and sina_main is not None:
            checked += 1
            diff = abs(f52_sum - sina_main) / max(abs(sina_main), 1.0)
            ok = diff <= args.ratio_tol
            cross_note = f"{diff*100:.1f}%"
            if ok:
                cross_ok += 1
        print(f"  {kind} {code}: klt5 {len(bars5)}根(末根 {last['time'] if last else '-'}, 距今 {age_min if age_min is not None else '-'}min) "
              f"klt1 {len(bars1)}根 | 当日f52累加 {f52_sum} vs 新浪main {sina_main} 偏差 {cross_note}")
        time.sleep(0.5)

    report("klt=5 可用", klt5_ok == len(samples), f"{klt5_ok}/{len(samples)} 只")
    report("klt=1 可用", klt1_ok == len(samples), f"{klt1_ok}/{len(samples)} 只", hard=False)
    if in_session:
        report(f"新鲜度≤{args.fresh_min}min（交易时段硬门槛）", fresh_ok == fresh_checked and fresh_checked > 0,
               f"{fresh_ok}/{fresh_checked} 只达标")
    else:
        print(f"[提示] 非交易时段，跳过新鲜度硬门槛（{fresh_ok}/{fresh_checked} 只 ≤{args.fresh_min}min）")
    report(f"口径交叉≤{args.ratio_tol*100:.0f}%（硬门槛）", checked > 0 and cross_ok == checked,
           f"{cross_ok}/{checked} 只达标")

    print(f"\n结论: {'全部 PASS' if not any(r[3] and not r[1] for r in RESULTS) else '存在硬性 FAIL'}")
    return 0 if not any(r[3] and not r[1] for r in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
