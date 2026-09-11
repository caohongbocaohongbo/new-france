#!/usr/bin/env python3
"""M0 验证 D：涨停池 sina_calc 计算 + 东财 zt_pool 可达性 + 同花顺涨停复盘试探（24 方案）。一次性脚本。
sina_calc 判定：主板非ST changeratio≥9.8%；ST≥4.8%；创业/科创≥19.5%。changeratio 归一化为百分数。
输出：疑似涨停清单数量 + 抽样；东财/同花顺可达性记录。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests

H = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
BULK = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_bkzj_ssggzj"


def report(name, ok, detail, hard=True):
    print(f"[{'PASS' if ok else ('FAIL' if hard else 'WARN')}] {name}: {detail}")


def is_zt(code, name, chg_pct):
    """chg_pct 为百分数（如 10.02）。返回 (是否疑似涨停, 板块)。"""
    st = "ST" in str(name).upper()
    if code.startswith(("300", "301", "688")):
        lim = 19.5
    elif st:
        lim = 4.8
    else:
        lim = 9.8
    return chg_pct >= lim, ("创/科" if code.startswith(("300", "301", "688")) else ("ST" if st else "主板"))


def main():
    rows = requests.get(BULK, params={"num": "8000", "sort": "r0_net", "asc": "0"}, headers=H, timeout=20).json()
    hits = []
    for x in rows:
        code = str(x.get("symbol"))[2:].zfill(6)
        name = x.get("name") or ""
        chg = x.get("changeratio")
        if chg in (None, ""):
            continue
        pct = float(chg)
        if pct > 1:  # 若已是百分数
            pct = pct
        else:
            pct = pct * 100
        z, board = is_zt(code, name, pct)
        if z:
            hits.append((code, name, round(pct, 2), board))
    report("sina_calc 涨停清单可算", len(hits) > 0, f"疑似涨停 {len(hits)} 只")
    for h in hits[:10]:
        print("  ", h)
    # 东财 zt_pool 可达性
    try:
        from backend.agents.layer1_data_collector.sources.eastmoney_zt import fetch_zt_pool
        zt = fetch_zt_pool()
        report("东财 zt_pool 可达(备用源)", zt is not None and not zt.empty, f"{'有' if zt is not None else '无'}数据", hard=False)
    except Exception as e:
        report("东财 zt_pool 可达(备用源)", False, f"异常 {e}")
    # 同花顺涨停复盘试探（记录可达性，不判硬性）
    try:
        r = requests.get("http://data.10jqka.com.cn/market/longhu/", headers={"User-Agent": "Mozilla/5.0", "Referer": "http://data.10jqka.com.cn/"}, timeout=8)
        report("同花顺涨停复盘可达性", r.status_code == 200, f"http {r.status_code}（仅试探，解析未做）", hard=False)
    except Exception as e:
        report("同花顺涨停复盘可达性", False, f"异常 {e}", hard=False)
    print("结论:", "PASS" if len(hits) > 0 else "FAIL")
    return 0 if len(hits) > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
