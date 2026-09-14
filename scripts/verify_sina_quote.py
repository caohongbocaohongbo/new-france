#!/usr/bin/env python3
"""新浪bulk单位、覆盖与腾讯交叉核验；缺时间戳时不授予实时准入。"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests
from backend.agents.layer1_data_collector.sources.quote_contract import (
    normalize_sina_bulk, parse_tencent_quotes, quote_is_current,
)
from scripts.source_verification import Checks

H = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
BULK = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_bkzj_ssggzj"
ROOT = Path(__file__).resolve().parents[1]


def main():
    checks = Checks()
    try:
        response = requests.get(BULK, params={"num": 8000, "sort": "r0_net", "asc": 0}, headers=H, timeout=(3, 8))
        response.raise_for_status()
        rows = normalize_sina_bulk(response.json())
        checks.check("全返回字段", all(all(row[k] is not None for k in
                      ("price", "change_pct", "turnover_pct", "amount")) for row in rows), f"{len(rows)}行")
        cache = ROOT / "data/principal_capital_sina_codes.json"
        if cache.exists():
            wanted = set(json.loads(cache.read_text(encoding="utf-8")).get("codes") or [])
            got = {row["code"] for row in rows}
            checks.check("现有清单覆盖", bool(wanted) and wanted <= got, f"缺{len(wanted - got)}只")
            checks.skip("当日主数据完整性", "缓存清单不能证明当日新上市/停牌范围已核验")
        else:
            checks.skip("主板覆盖", "无已验证股票清单")
        sample = sorted((r for r in rows if r["price"] and r["turnover_pct"] is not None),
                        key=lambda r: r["turnover_pct"], reverse=True)[:20]
        symbols = [row["symbol"] for row in sample]
        if not symbols:
            raise ValueError("没有可交叉验证的样本")
        response = requests.get("https://qt.gtimg.cn/q=" + ",".join(symbols), headers=H, timeout=(3, 8))
        response.raise_for_status()
        response.encoding = "gbk"
        tx = {r["代码"]: r for r in parse_tencent_quotes(response.text, symbols)}
        for row in sample:
            peer = tx.get(row["code"])
            if not checks.check(row["code"] + " 独立报价", bool(peer)):
                continue
            checks.check(row["code"] + " 腾讯时效", quote_is_current(peer))
            turn = peer["换手率"]
            checks.check(row["code"] + " 换手率单位",
                         turn is not None and abs(row["turnover_pct"] - turn) <= max(0.05, abs(turn) * 0.02),
                         f"新浪{row['turnover_pct']:.4f}% / 腾讯{turn}%")
        checks.skip("实时及资金策略准入", "bulk无明确源时间，且缺r1/r2分类；仅用于报价观察")
    except Exception as exc:
        checks.check("新浪bulk核验", False, str(exc))
    return checks.finish()


if __name__ == "__main__":
    sys.exit(main())
