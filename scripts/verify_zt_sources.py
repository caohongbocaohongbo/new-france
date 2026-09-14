#!/usr/bin/env python3
"""基础涨停状态抽样：使用新鲜腾讯报价及涨停价，不用涨幅阈值猜测。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests
from backend.agents.layer1_data_collector.sources.quote_contract import (
    normalize_sina_bulk, parse_tencent_quotes, quote_is_current, at_limit_price,
)
from scripts.source_verification import Checks
from scripts.verify_sina_quote import H, BULK


def main():
    checks = Checks()
    try:
        response = requests.get(BULK, params={"num": 8000, "sort": "r0_net", "asc": 0}, headers=H, timeout=(3, 8))
        response.raise_for_status()
        universe = normalize_sina_bulk(response.json())
        sample = sorted((r for r in universe if r["change_pct"] is not None),
                        key=lambda r: r["change_pct"], reverse=True)[:60]
        symbols = [row["symbol"] for row in sample]
        if not symbols:
            raise ValueError("没有可核验报价")
        response = requests.get("https://qt.gtimg.cn/q=" + ",".join(symbols), headers=H, timeout=(3, 8))
        response.raise_for_status()
        response.encoding = "gbk"
        quotes = parse_tencent_quotes(response.text, symbols)
        checks.check("抽样覆盖", len(quotes) == len(symbols), f"{len(quotes)}/{len(symbols)}")
        checks.check("抽样报价时效", bool(quotes) and all(quote_is_current(row) for row in quotes))
        confirmed, unknown = [], []
        for row in quotes:
            state = at_limit_price(row["最新价"], row["涨停价"])
            if state is None:
                unknown.append(row["代码"])
            elif state:
                confirmed.append((row["代码"], row["名称"]))
        print(f"抽样当前处于涨停价: {len(confirmed)}，规则价未知: {len(unknown)}；零涨停也是合法结果")
        for item in confirmed[:10]:
            print(item)
        if unknown:
            checks.skip("部分涨停价", ",".join(unknown))
        checks.skip("全市场涨停池/封板事件", "仅验证最多60只基础状态，未验证全市场覆盖、首封和炸板事件")
    except Exception as exc:
        checks.check("基础涨停抽样", False, str(exc))
    return checks.finish()


if __name__ == "__main__":
    sys.exit(main())
