#!/usr/bin/env python3
"""腾讯批量报价及日K验证；跨除权日比较必须真实通过才能完成M0。"""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import date, datetime
import requests
from backend.agents.layer1_data_collector.sources.quote_contract import parse_tencent_quotes, quote_is_current
from backend.agents.layer1_data_collector.sources.historical_kline import (
    _fetch_hist_tencent, _fetch_hist_eastmoney_direct, BEIJING_TZ,
)
from scripts.source_verification import Checks, validate_bars, compare_closes, check_recency

H = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
CODES = ["sh600519", "sz000001", "sh600036", "sz000858", "sh601318",
         "sh601398", "sz000333", "sh600000", "sz002415", "sh601088",
         "sz000651", "sh600028", "sz000002", "sh601857", "sz000100",
         "sh600900", "sz002594", "sh601166", "sz000725", "sh600050"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ex-date", type=date.fromisoformat, help="已核实的样本除权日期")
    args = parser.parse_args(argv)
    checks = Checks()
    try:
        response = requests.get("https://qt.gtimg.cn/q=" + ",".join(CODES), headers=H, timeout=(3, 8))
        response.raise_for_status()
        response.encoding = "gbk"
        rows = parse_tencent_quotes(response.text, CODES)
        checks.check("批量代码覆盖", len(rows) == len(CODES), f"{len(rows)}/{len(CODES)}")
        checks.check("必需字段完整", bool(rows) and all(not row["degraded"] for row in rows))
        checks.check("报价时间", bool(rows) and all(quote_is_current(row) for row in rows))
    except Exception as exc:
        checks.check("腾讯报价", False, str(exc))
    for symbol in CODES[:3]:
        try:
            tx = _fetch_hist_tencent(symbol[2:], 180)
            if not checks.check(symbol + " 日K", validate_bars(tx)):
                continue
            checks.check(symbol + " 复权身份", tx.attrs.get("adjustment") == "qfq")
            check_recency(checks, tx, datetime.now(BEIJING_TZ))
            em = _fetch_hist_eastmoney_direct(symbol[2:], 180)
            if em is None or em.empty:
                checks.skip(symbol + " 独立对照", "东财不可用，不能视作复权验证成功")
                continue
            ok, detail = compare_closes(tx, em, min_rows=120)
            checks.check(symbol + " 按交易日比较", ok, detail)
            if args.ex_date:
                shared = sorted(set(tx["日期"].astype(str)) & set(em["日期"].astype(str)))
                day = args.ex_date.isoformat()
                checks.check(symbol + " 除权日期覆盖", day in shared and shared[0] < day < shared[-1])
        except Exception as exc:
            checks.check(symbol, False, str(exc))
    if args.ex_date is None:
        checks.skip("除权事件证据", "未指定已核实样本除权日期；普通重合区间不能证明复权")
    return checks.finish()


if __name__ == "__main__":
    sys.exit(main())
