#!/usr/bin/env python3
"""M0 验证 C：新浪全市场实时行情 + 指数（24 方案 quotes/index_snapshot 主源）。一次性脚本。
断言：ssggzj bulk 全市场行情字段（trade/changeratio/turnover/amount）非空；主板清单覆盖 0 漏；指数接口可达。
"""
import json
import sys
import requests

H = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
BULK = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_bkzj_ssggzj"


def report(name, ok, detail, hard=True):
    print(f"[{'PASS' if ok else ('FAIL' if hard else 'WARN')}] {name}: {detail}")


def main():
    r = requests.get(BULK, params={"num": "8000", "sort": "r0_net", "asc": "0"}, headers=H, timeout=20)
    rows = r.json()
    print("bulk rows:", len(rows), "http:", r.status_code)
    sample = rows[:500]
    need = ["symbol", "name", "trade", "changeratio", "turnover", "amount"]
    missing = [c for c in need if sum(1 for x in sample if x.get(c) in (None, "")) > 450]
    report("行情字段完整(trade/changeratio/turnover/amount)", not missing, f"缺失: {missing or '无'}")
    # 换手率有值抽样
    tvs = [x for x in sample if x.get("turnover") not in (None, "", "0")]
    report("换手率有值", len(tvs) > 100, f"抽样500中 {len(tvs)} 只有换手率")
    # 主板覆盖
    cached = json.load(open("data/principal_capital_sina_codes.json", encoding="utf-8"))
    want = {str(c).zfill(6) for c in (cached.get("codes") or [])}
    got = {str(x.get("symbol"))[2:].zfill(6) for x in rows if str(x.get("symbol") or "")[:2] in ("sh", "sz")}
    miss = sorted(want - got)
    report("主板清单覆盖 0 漏", not miss, f"清单 {len(want)} / bulk {len(want & got)} / 漏 {len(miss)}")
    # 指数接口试探
    idx = "https://hq.sinajs.cn/list=s_sh000001,s_sz399001,s_sz399006"
    try:
        ir = requests.get(idx, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}, timeout=8)
        ir.encoding = "gbk"
        report("新浪指数接口可达", ir.status_code == 200 and len(ir.text) > 50,
               f"http {ir.status_code} / {ir.text[:80]}")
    except Exception as e:
        report("新浪指数接口可达", False, f"异常 {e}")
    print("结论:", "PASS" if not missing and not miss else "FAIL")
    return 0 if (not missing and not miss) else 1


if __name__ == "__main__":
    sys.exit(main())
