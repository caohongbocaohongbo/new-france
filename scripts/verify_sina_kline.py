#!/usr/bin/env python3
"""新浪轻量日K硬校验；复权未知时退出2，不宣布可作前复权主源。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import datetime
from backend.agents.layer1_data_collector.sources.historical_kline import _fetch_hist_sina, BEIJING_TZ
from scripts.source_verification import Checks, validate_bars, check_recency

CODES = ["600519", "000001", "600036", "000858", "601318"]


def main():
    checks = Checks()
    for code in CODES:
        try:
            df = _fetch_hist_sina(code, 60)
            if not checks.check(f"{code} OHLCV/日期", validate_bars(df)):
                continue
            check_recency(checks, df, datetime.now(BEIJING_TZ))
            checks.check(f"{code} 缺口保持空值",
                         all(df[key].isna().all() for key in ("成交额", "换手率", "振幅")))
            checks.check(f"{code} 复权标记真实", df.attrs.get("adjustment") == "unknown")
        except Exception as exc:
            checks.check(code, False, str(exc))
    checks.skip("前复权准入", "scale=240只表示周期，尚未取得可验证的复权契约")
    return checks.finish()


if __name__ == "__main__":
    sys.exit(main())
