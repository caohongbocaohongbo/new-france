#!/usr/bin/env python3
"""验证脚本 1：新浪全市场资金流「单请求榜单」接口（23 方案施工前置验证，一次性脚本，不进主链路）。

背景：生产源健康状态实测——eastmoney 连续失败 66 次、akshare 33 次（均熔断）；sina 为唯一健康源，
当前每轮 3195 只逐票请求（MoneyFlow.ssi_ssfx_flzjtj）。本脚本验证新浪 MoneyFlow.ssl_bkzj_ssggzj
能否单请求取全市场 + 排序，以及「ratioamount 粗筛圈定 → 逐票精算」口径是否不漏、请求量削减多少。

验证项：
  A 单请求全市场：num=8000 返回全市场行数 + 延迟 + 字段完整
  B r0_net 降序/升序单调（买榜/卖榜直接截头即得）
  C 覆盖性：bulk 主板代码集合 vs 本地主板清单缓存，漏码 = 0 硬性通过
  D 粗筛圈定效率：ratioamount 阈值圈出的票数 + 对圈定集逐票精算 main_ratio，验证「精算候选(≥50%) ⊆ 圈定集」
     并统计精算请求量（相对 3195 的削减比例）
用法：python3 scripts/verify_fund_flow_leaderboard.py [--coarse-ratio 40]
退出码：存在硬性 FAIL → 1；仅 WARN → 0。
"""
import argparse
import sys
import time

import requests

H = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
BULK_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "MoneyFlow.ssl_bkzj_ssggzj")
SINGLE_URL = ("http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
              "MoneyFlow.ssi_ssfx_flzjtj")
MAIN_BOARD_PREFIXES = ("60", "000", "001", "002", "003")
BUY_MAIN_RATIO = 50.0
SELL_MAIN_RATIO = 30.0
RESULTS = []


def report(name, ok, detail, hard=True):
    RESULTS.append((name, ok, detail, hard))
    print(f"[{'PASS' if ok else ('FAIL' if hard else 'WARN')}] {name}: {detail}")


def _float(v):
    try:
        return None if v in (None, "", "-") else float(v)
    except (TypeError, ValueError):
        return None


def fetch_bulk(num=8000, sort="r0_net", asc=0, timeout=20):
    params = {"num": str(num)}
    if sort:
        params["sort"] = sort
        params["asc"] = str(asc)
    t0 = time.perf_counter()
    resp = requests.get(BULK_URL, params=params, headers=H, timeout=timeout)
    ms = (time.perf_counter() - t0) * 1000
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"非列表返回: {str(data)[:120]}")
    return data, ms


def fetch_single(code):
    prefix = "sh" if str(code).zfill(6).startswith(("5", "6", "9")) else "sz"
    resp = requests.get(SINGLE_URL, params={"daima": prefix + str(code).zfill(6)},
                        headers=H, timeout=8)
    resp.raise_for_status()
    data = resp.json()
    item = data[0] if isinstance(data, list) else data
    r0i, r0o = _float(item.get("r0_in")), _float(item.get("r0_out"))
    r1i, r1o = _float(item.get("r1_in")), _float(item.get("r1_out"))
    r2i, r2o = _float(item.get("r2_in")), _float(item.get("r2_out"))
    r3i, r3o = _float(item.get("r3_in")), _float(item.get("r3_out"))
    total = (r0i or 0) + (r0o or 0) + (r1i or 0) + (r1o or 0) + (r2i or 0) + (r2o or 0) + (r3i or 0) + (r3o or 0)
    main_net = (r0i - r0o) + (r1i - r1o)
    ratio = main_net / total * 100 if total > 0 else None
    return main_net, ratio


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--coarse-ratio", type=float, default=40.0)
    args = parser.parse_args()

    # A：单请求全市场
    try:
        rows, ms = fetch_bulk(num=8000, sort="r0_net", asc=0)
    except Exception as exc:  # noqa: BLE001
        report("ssggzj 单请求全市场", False, f"异常: {exc}")
        return 1
    report("ssggzj 单请求全市场", len(rows) >= 5000, f"{len(rows)} 行 / {ms:.0f}ms（替代 3195 次逐票请求）")
    need = ["symbol", "name", "trade", "changeratio", "amount", "r0_net", "r0_ratio", "r3_net",
            "netamount", "ratioamount"]
    missing = [c for c in need if sum(1 for r in rows[:500] if r.get(c) in (None, "")) > 450]
    report("字段完整", not missing, f"缺失字段: {missing or '无'}")

    # B：排序单调
    nets = [_float(r.get("r0_net")) for r in rows if _float(r.get("r0_net")) is not None]
    report("r0_net 降序单调", all(nets[i] >= nets[i + 1] for i in range(len(nets) - 1)), f"{len(nets)} 条")
    try:
        asc_rows, _ = fetch_bulk(num=8000, sort="r0_net", asc=1)
        asc_nets = [_float(r.get("r0_net")) for r in asc_rows if _float(r.get("r0_net")) is not None]
        report("r0_net 升序单调(卖榜)", all(asc_nets[i] <= asc_nets[i + 1] for i in range(len(asc_nets) - 1)),
               f"{len(asc_nets)} 条")
    except Exception as exc:  # noqa: BLE001
        asc_rows = []
        report("r0_net 升序单调(卖榜)", False, f"异常: {exc}")

    # C：覆盖性（主板清单 0 漏码）
    codes_file = "data/principal_capital_sina_codes.json"
    try:
        import json
        cached = json.load(open(codes_file, encoding="utf-8"))
        if isinstance(cached, dict):
            cached = cached.get("codes") or []
        want = {str(c).zfill(6) for c in cached}
    except Exception:
        want = set()
    got = {str(r.get("symbol"))[2:].zfill(6) for r in rows
           if str(r.get("symbol") or "")[:2] in ("sh", "sz")
           and str(r.get("symbol"))[2:].zfill(6).startswith(MAIN_BOARD_PREFIXES)}
    if want:
        missing_codes = sorted(want - got)
        report("覆盖性·主板清单无漏码", not missing_codes,
               f"清单 {len(want)} 只 / bulk 主板 {len(got)} 只 / 漏 {len(missing_codes)}: {missing_codes[:8]}")
    else:
        report("覆盖性·主板清单无漏码", False, f"本地主板清单缓存不可用({codes_file})", hard=False)

    # D：粗筛圈定 + 精算不漏 + 请求量削减
    coarse = [r for r in rows
              if str(r.get("symbol") or "")[:2] in ("sh", "sz")
              and str(r.get("symbol"))[2:].zfill(6).startswith(MAIN_BOARD_PREFIXES)
              and (_float(r.get("ratioamount")) or 0) * 100 >= args.coarse_ratio]
    coarse_sell = [r for r in rows
                   if str(r.get("symbol") or "")[:2] in ("sh", "sz")
                   and str(r.get("symbol"))[2:].zfill(6).startswith(MAIN_BOARD_PREFIXES)
                   and (_float(r.get("ratioamount")) or 0) * 100 <= -args.coarse_ratio * 0.6]
    print(f"  粗筛圈定: 买侧 ratioamount≥{args.coarse_ratio}% → {len(coarse)} 只 | 卖侧 ≤-{args.coarse_ratio*0.6:.0f}% → {len(coarse_sell)} 只")
    # P3（grill 实测修正）：买+卖圈定集全量精算（卖侧头部 150 实测漏检 83% → 必须全量），并发 20
    from concurrent.futures import ThreadPoolExecutor

    refine_total = 0
    t0_refine = time.perf_counter()
    results_map = {}
    targets = list(coarse) + list(coarse_sell)

    def _refine_one(r):
        code = str(r.get("symbol"))[2:].zfill(6)
        try:
            return code, fetch_single(code)[1]
        except Exception:
            return code, None

    with ThreadPoolExecutor(max_workers=20) as pool:
        for code, ratio in pool.map(_refine_one, targets):
            results_map[code] = ratio
            if ratio is not None:
                refine_total += 1
    refine_ms = (time.perf_counter() - t0_refine) * 1000
    buys = [(str(r.get("symbol"))[2:].zfill(6), round(v, 2)) for r in coarse
            for v in [results_map.get(str(r.get("symbol"))[2:].zfill(6))]
            if v is not None and v >= BUY_MAIN_RATIO]
    sells = [(str(r.get("symbol"))[2:].zfill(6), round(v, 2)) for r in coarse_sell
             for v in [results_map.get(str(r.get("symbol"))[2:].zfill(6))]
             if v is not None and v <= -SELL_MAIN_RATIO]
    print(f"  圈定精算 {refine_total} 只(并发20, {refine_ms:.0f}ms) → 买 main≥50% 共 {len(buys)} 只: {buys[:5]}")
    print(f"  卖 main≤-30% 共 {len(sells)} 只: {sells[:5]}")
    # P3 反证：圈定线之外（ratioamount > -24%）随机 20 只，断言无 main≤-30% 越界（圈定线本身不漏）
    import random

    outside_sell = [r for r in rows
                    if str(r.get("symbol") or "")[:2] in ("sh", "sz")
                    and str(r.get("symbol"))[2:].zfill(6).startswith(MAIN_BOARD_PREFIXES)
                    and (_float(r.get("ratioamount")) or 0) * 100 > -24][:2000]
    sample_n = min(20, len(outside_sell))
    sell_violators = []
    for r in random.sample(outside_sell, sample_n) if sample_n else []:
        code = str(r.get("symbol"))[2:].zfill(6)
        try:
            _, ratio = fetch_single(code)
        except Exception:
            continue
        if ratio is not None and ratio <= -SELL_MAIN_RATIO:
            sell_violators.append((code, round(ratio, 2)))
    report("卖侧圈定线外反证(≤-30% 越界=0)", not sell_violators,
           f"圈定外随机精算 {sample_n} 只，越界 {len(sell_violators)}: {sell_violators[:5]}")
    # 反证：圈定外（ratioamount < 粗筛线）是否可能 main≥50%
    outside = [r for r in rows[:3000]
               if str(r.get("symbol") or "")[:2] in ("sh", "sz")
               and str(r.get("symbol"))[2:].zfill(6).startswith(MAIN_BOARD_PREFIXES)
               and 30.0 <= (_float(r.get("ratioamount")) or 0) * 100 < args.coarse_ratio][:10]
    violators = []
    for r in outside:
        code = str(r.get("symbol"))[2:].zfill(6)
        try:
            _, ratio = fetch_single(code)
            if ratio is not None and ratio >= BUY_MAIN_RATIO:
                violators.append((code, ratio))
        except Exception:
            continue
    report("粗筛不漏(圈定外无 main≥50%)", not violators,
           f"抽样圈定外 {len(outside)} 只精算，越界 {len(violators)}: {violators[:5]}")
    saved = 1 - (1 + refine_total) / 3195
    report("请求削减", saved > 0.8, f"3195 → 1(bulk) + {refine_total} 精算 = 削减 {saved*100:.0f}%")

    print(f"\n结论: {'全部 PASS' if not any(r[3] and not r[1] for r in RESULTS) else '存在硬性 FAIL'}")
    return 0 if not any(r[3] and not r[1] for r in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
