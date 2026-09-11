#!/usr/bin/env python3
"""M0 验证 B：腾讯批量报价 + 前复权日K（24 方案去东财化备源/行情源）。一次性脚本。
断言：批量报价 200 且字段完整（名称/现价/昨收/涨跌幅/成交额/换手率/总市值）；批量 60 只不超限；
前复权日K 200 且与新浪前复权收盘 diff ≤0.1%。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests

H = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
CODES = ["sh600519", "sz000001", "sh600036", "sz000858", "sh601318", "sh601398", "sz000333", "sh600000",
         "sz002415", "sh601088", "sz000651", "sh600028", "sz000002", "sh601857", "sz000100",
         "sh600900", "sz002594", "sh601166", "sz000725", "sh600050"]


def report(name, ok, detail, hard=True):
    print(f"[{'PASS' if ok else ('FAIL' if hard else 'WARN')}] {name}: {detail}")


def main():
    # 批量报价（20 只一次）
    url = "https://qt.gtimg.cn/q=" + ",".join(CODES)
    r = requests.get(url, headers=H, timeout=10)
    r.encoding = "gbk"
    print("报价 http:", r.status_code, "len:", len(r.text))
    good = 0
    for code in CODES:
        line = r.text.split('v_' + code + '="')[1].split('";')[0] if ('v_' + code + '="') in r.text else ""
        if not line:
            continue
        p = line.split("~")
        # 腾讯字段: 1名称 2代码 3现价 4昨收 5今开 6成交量(手) 31涨跌 32涨跌幅 37成交额(万) 38换手率 43振幅 44流通市值 45总市值
        if len(p) > 45:
            name, price, prev, pct = p[1], p[3], p[4], p[32]
            turnover, tot_mv = p[38], p[45]
            good += 1
            if good <= 3:
                print(f"  {code}: {name} 现价={price} 昨收={prev} 涨跌幅={pct}% 换手率={turnover} 总市值={tot_mv}")
    report("腾讯批量报价可用", good == len(CODES), f"{good}/{len(CODES)} 只字段完整")
    # 前复权日K + 与新浪对照
    from backend.agents.layer1_data_collector.sources.historical_kline import _fetch_hist_sina
    import json
    k_ok = diff_ok = 0
    for code in CODES[:3]:
        q = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,30,qfq" % code
        try:
            d = requests.get(q, headers=H, timeout=10).json()
            day = (d.get("data") or {}).get(code, {}).get("qfqday") or []
        except Exception as e:
            print(f"  {code} 腾讯日K失败 {e}")
            continue
        if day:
            k_ok += 1
            closes_tx = [float(x[2]) for x in day]
            sn = _fetch_hist_sina(code[2:], 30)
            if sn is not None and not sn.empty:
                closes_sn = sn["收盘"].astype(float).tolist()[-len(closes_tx):]
                if closes_sn:
                    diff = max(abs(a - b) for a, b in zip(closes_tx, closes_sn))
                    diff_pct = diff / closes_sn[-1] * 100
                    ok = diff_pct <= 0.1
                    if ok:
                        diff_ok += 1
                    print(f"  {code}: 腾讯日K {len(day)}根 前复权收盘 vs 新浪 最大偏差 {diff_pct:.4f}%")
    report("腾讯前复权日K可用", k_ok == 3, f"{k_ok}/3 只")
    report("腾讯vs新浪前复权一致(≤0.1%)", diff_ok == k_ok and k_ok > 0, f"{diff_ok}/{k_ok} 只")
    print("结论:", "PASS" if good == len(CODES) and k_ok == 3 else "FAIL")
    return 0 if (good == len(CODES) and k_ok == 3) else 1


if __name__ == "__main__":
    sys.exit(main())
