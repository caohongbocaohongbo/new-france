import asyncio
import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from backend.plugins.overnight_arbitrage.service import (
    HISTORY_FILE,
    REPORT_FILE,
    run_overnight_arbitrage,
)


class OvernightArbitragePipelineTest(unittest.TestCase):
    def test_run_pipeline_writes_latest_and_history_without_email_in_test(self):
        report_text = REPORT_FILE.read_text(encoding="utf-8") if REPORT_FILE.exists() else None
        history_text = HISTORY_FILE.read_text(encoding="utf-8") if HISTORY_FILE.exists() else None
        quotes = pd.DataFrame([{
            "代码": "600001", "名称": "主板强势", "最新价": 12.3, "涨跌幅": 9.82,
            "最高价": 12.6, "成交额": 420_000_000, "换手率": 7.2,
            "量比": 2.8, "市盈率": 18.6, "流通市值": 8_800_000_000,
        }])
        zt_pool = pd.DataFrame([{"代码": "600001", "封板时间": 144100, "炸板次数": 0, "连板数": 1}])

        try:
            with patch("backend.plugins.overnight_arbitrage.service.notify_overnight_decision", return_value=True):
                result = asyncio.run(run_overnight_arbitrage(
                    target_date=date(2026, 6, 4),
                    quote_fetcher=lambda: quotes,
                    zt_fetcher=lambda _target_date: zt_pool,
                    minute_fetcher=lambda _codes: {
                        "600001": {"last_15m_change_pct": 1.25, "last_close_position": 0.96}
                    },
                    dry_run=False,
                ))

            self.assertEqual(result["history_summary"]["status"], "completed")
            self.assertEqual(result["results"][0]["history"]["recommendation_count"], 1)
            self.assertTrue(REPORT_FILE.exists())
            self.assertTrue(HISTORY_FILE.exists())
        finally:
            if report_text is None:
                REPORT_FILE.unlink(missing_ok=True)
            else:
                REPORT_FILE.write_text(report_text, encoding="utf-8")
            if history_text is None:
                HISTORY_FILE.unlink(missing_ok=True)
            else:
                HISTORY_FILE.write_text(history_text, encoding="utf-8")

    def test_run_pipeline_reports_runtime_dns_channel_failure(self):
        def _dns_failure():
            raise RuntimeError(
                "HTTPSConnectionPool(host='push2.eastmoney.com', port=443): "
                "Max retries exceeded (Caused by NameResolutionError(\"Failed to resolve 'push2.eastmoney.com' "
                "([Errno 8] nodename nor servname provided, or not known)\"))"
            )

        def _dns_failure_backup(*_args, **_kwargs):
            raise RuntimeError(
                "HTTPSConnectionPool(host='vip.stock.finance.sina.com.cn', port=443): "
                "Max retries exceeded (Caused by NameResolutionError(\"Failed to resolve 'vip.stock.finance.sina.com.cn' "
                "([Errno 8] nodename nor servname provided, or not known)\"))"
            )

        with patch("backend.plugins.overnight_arbitrage.service._sina_all_a_snapshot", side_effect=_dns_failure_backup), \
                patch("backend.plugins.overnight_arbitrage.service.notify_overnight_decision", return_value=False):
            result = asyncio.run(run_overnight_arbitrage(
                target_date=date(2026, 6, 8),
                quote_fetcher=_dns_failure,
                zt_fetcher=lambda _target_date: pd.DataFrame(),
                minute_fetcher=lambda _codes: {},
                dry_run=True,
            ))

        self.assertEqual(result["status"], "data_unavailable")
        self.assertEqual(result["source_status"]["channel"]["kind"], "dns_resolution_failed")
        self.assertEqual(result["source_status"]["channel"]["scope"], "runtime_environment")
        self.assertIn("DNS/外网通道异常", result["empty_reason"])


if __name__ == "__main__":
    unittest.main()
