import asyncio
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from backend.plugins.overnight_arbitrage.service import (
    HISTORY_FILE,
    REPORT_FILE,
    run_overnight_arbitrage,
    update_overnight_history,
    write_overnight_report,
)


class OvernightArbitragePipelineTest(unittest.TestCase):
    BEIJING_TZ = timezone(timedelta(hours=8))

    @staticmethod
    def _quotes(volume_ratio=2.8):
        return pd.DataFrame([{
            "代码": "600001", "名称": "主板强势", "最新价": 12.3, "涨跌幅": 9.82,
            "最高价": 12.6, "成交额": 420_000_000, "换手率": 7.2,
            "量比": volume_ratio, "市盈率": 18.6, "流通市值": 8_800_000_000,
        }])

    @staticmethod
    def _zt_pool():
        return pd.DataFrame([{"代码": "600001", "封板时间": 144100, "炸板次数": 0, "连板数": 1}])

    def _run_isolated(self, *, now, quotes, notification_state_file, zt_pool=None):
        if zt_pool is None:
            zt_pool = self._zt_pool()
        with patch("backend.plugins.overnight_arbitrage.service.write_overnight_report"), \
                patch("backend.plugins.overnight_arbitrage.service.update_overnight_history", return_value={
                    "status": "completed", "records": [], "total_stocks": 0,
                    "total_recommendations": 0, "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                }):
            return asyncio.run(run_overnight_arbitrage(
                target_date=now.date(),
                quote_fetcher=lambda: quotes,
                zt_fetcher=lambda _target_date: zt_pool,
                minute_fetcher=lambda _codes: {
                    "600001": {"last_15m_change_pct": 1.25, "last_close_position": 0.96}
                },
                dry_run=False,
                current_time=now,
                notification_state_file=notification_state_file,
            ))

    def test_pipeline_blocks_official_email_outside_valid_window(self):
        now = datetime(2026, 6, 4, 17, 1, tzinfo=self.BEIJING_TZ)
        with TemporaryDirectory() as tmp, \
                patch("backend.plugins.overnight_arbitrage.service.notify_overnight_decision") as notify:
            result = self._run_isolated(
                now=now,
                quotes=self._quotes(),
                notification_state_file=Path(tmp) / "sent.json",
            )

        notify.assert_not_called()
        self.assertFalse(result["notification"]["eligible"])
        self.assertIn("outside_valid_window", result["notification"]["blocked_reasons"])

    def test_pipeline_blocks_official_email_when_volume_ratio_is_missing(self):
        now = datetime(2026, 6, 4, 14, 45, tzinfo=self.BEIJING_TZ)
        with TemporaryDirectory() as tmp, \
                patch("backend.plugins.overnight_arbitrage.service.notify_overnight_decision") as notify:
            result = self._run_isolated(
                now=now,
                quotes=self._quotes(volume_ratio=None),
                notification_state_file=Path(tmp) / "sent.json",
            )

        notify.assert_not_called()
        self.assertEqual(result["results"], [])
        self.assertEqual(result["data_quality"]["status"], "complete")
        self.assertEqual(result["data_quality"]["removed_count"], 1)
        self.assertEqual(result["data_quality"]["removed"][0], {
            "code": "600001",
            "name": "主板强势",
            "missing_fields": ["volume_ratio"],
        })
        self.assertIn("no_complete_candidates", result["notification"]["blocked_reasons"])
        self.assertNotIn("incomplete_quote_fields", result["notification"]["blocked_reasons"])

    def test_pipeline_sends_complete_candidates_after_removing_incomplete_quotes(self):
        now = datetime(2026, 6, 4, 14, 45, tzinfo=self.BEIJING_TZ)
        quotes = pd.DataFrame([
            {
                "代码": "600001", "名称": "主板强势", "最新价": 12.3, "涨跌幅": 9.82,
                "最高价": 12.6, "成交额": 420_000_000, "换手率": 7.2,
                "量比": None, "市盈率": 18.6, "流通市值": 8_800_000_000,
            },
            {
                "代码": "000002", "名称": "主板完整", "最新价": 9.3, "涨跌幅": 9.2,
                "最高价": 9.5, "成交额": 360_000_000, "换手率": 6.5,
                "量比": 2.4, "市盈率": 16.8, "流通市值": 7_600_000_000,
            },
        ])
        with TemporaryDirectory() as tmp, \
                patch("backend.plugins.overnight_arbitrage.service.notify_overnight_decision", return_value=True) as notify:
            result = self._run_isolated(
                now=now,
                quotes=quotes,
                notification_state_file=Path(tmp) / "sent.json",
            )

        self.assertEqual([item["code"] for item in result["results"]], ["000002"])
        self.assertGreater(result["data_quality"]["removed_count"], 0)
        self.assertEqual(result["data_quality"]["removed"][0], {
            "code": "600001",
            "name": "主板强势",
            "missing_fields": ["volume_ratio"],
        })
        self.assertTrue(result["notification"]["eligible"])
        self.assertTrue(result["notification"]["sent"])
        self.assertNotIn("no_complete_candidates", result["notification"]["blocked_reasons"])
        self.assertNotIn("incomplete_quote_fields", result["notification"]["blocked_reasons"])
        notify.assert_called_once()
        notified_decision = notify.call_args.args[0]
        self.assertEqual([item["code"] for item in notified_decision["results"]], ["000002"])
        self.assertEqual(notified_decision["data_quality"]["removed_count"], 1)
        self.assertEqual(notified_decision["data_quality"]["removed"], [{
            "code": "600001",
            "name": "主板强势",
            "missing_fields": ["volume_ratio"],
        }])

    def test_pipeline_blocks_official_email_when_today_zt_pool_is_unavailable(self):
        now = datetime(2026, 6, 4, 14, 45, tzinfo=self.BEIJING_TZ)
        with TemporaryDirectory() as tmp, \
                patch("backend.plugins.overnight_arbitrage.service.notify_overnight_decision") as notify:
            result = self._run_isolated(
                now=now,
                quotes=self._quotes(),
                zt_pool=pd.DataFrame(),
                notification_state_file=Path(tmp) / "sent.json",
            )

        notify.assert_not_called()
        self.assertIn("zt_pool_unavailable", result["notification"]["blocked_reasons"])

    def test_pipeline_blocks_official_email_on_weekend(self):
        now = datetime(2026, 6, 6, 14, 45, tzinfo=self.BEIJING_TZ)
        with TemporaryDirectory() as tmp, \
                patch("backend.plugins.overnight_arbitrage.service.notify_overnight_decision") as notify:
            result = self._run_isolated(
                now=now,
                quotes=self._quotes(),
                notification_state_file=Path(tmp) / "sent.json",
            )

        notify.assert_not_called()
        self.assertIn("non_trading_day", result["notification"]["blocked_reasons"])

    def test_pipeline_sends_only_once_at_valid_window_lower_boundary_with_complete_data(self):
        now = datetime(2026, 6, 4, 14, 40, tzinfo=self.BEIJING_TZ)
        with TemporaryDirectory() as tmp, \
                patch("backend.plugins.overnight_arbitrage.service.notify_overnight_decision", return_value=True) as notify:
            state_file = Path(tmp) / "sent.json"
            first = self._run_isolated(now=now, quotes=self._quotes(), notification_state_file=state_file)
            second = self._run_isolated(now=now, quotes=self._quotes(), notification_state_file=state_file)

        self.assertTrue(first["notification"]["sent"])
        self.assertFalse(second["notification"]["eligible"])
        self.assertIn("already_sent", second["notification"]["blocked_reasons"])
        notify.assert_called_once()

    def test_run_pipeline_writes_latest_and_history_without_email_in_test(self):
        quotes = pd.DataFrame([{
            "代码": "600001", "名称": "主板强势", "最新价": 12.3, "涨跌幅": 9.82,
            "最高价": 12.6, "成交额": 420_000_000, "换手率": 7.2,
            "量比": 2.8, "市盈率": 18.6, "流通市值": 8_800_000_000,
        }])
        zt_pool = pd.DataFrame([{"代码": "600001", "封板时间": 144100, "炸板次数": 0, "连板数": 1}])

        now = datetime(2026, 6, 4, 14, 45, tzinfo=self.BEIJING_TZ)
        with TemporaryDirectory() as tmp:
            report_file = Path(tmp) / REPORT_FILE.name
            history_file = Path(tmp) / HISTORY_FILE.name
            state_file = Path(tmp) / "sent.json"
            with patch(
                    "backend.plugins.overnight_arbitrage.service.write_overnight_report",
                    side_effect=lambda payload: write_overnight_report(payload, report_file=report_file),
                ), patch(
                    "backend.plugins.overnight_arbitrage.service.update_overnight_history",
                    side_effect=lambda payload: update_overnight_history(payload, history_file=history_file),
                ), patch(
                    "backend.plugins.overnight_arbitrage.service.notify_overnight_decision",
                    return_value=True,
                ):
                result = asyncio.run(run_overnight_arbitrage(
                    target_date=date(2026, 6, 4),
                    quote_fetcher=lambda: quotes,
                    zt_fetcher=lambda _target_date: zt_pool,
                    minute_fetcher=lambda _codes: {
                        "600001": {"last_15m_change_pct": 1.25, "last_close_position": 0.96}
                    },
                    dry_run=False,
                    current_time=now,
                    notification_state_file=state_file,
                ))

            self.assertEqual(result["history_summary"]["status"], "completed")
            self.assertEqual(result["results"][0]["history"]["recommendation_count"], 1)
            self.assertTrue(report_file.exists())
            self.assertTrue(history_file.exists())

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

        with TemporaryDirectory() as tmp, \
                patch("backend.plugins.overnight_arbitrage.service._sina_all_a_snapshot", side_effect=_dns_failure_backup), \
                patch("backend.plugins.overnight_arbitrage.service.write_overnight_report"), \
                patch("backend.plugins.overnight_arbitrage.service.notify_overnight_decision", return_value=False):
            result = asyncio.run(run_overnight_arbitrage(
                target_date=date(2026, 6, 8),
                quote_fetcher=_dns_failure,
                zt_fetcher=lambda _target_date: pd.DataFrame(),
                minute_fetcher=lambda _codes: {},
                dry_run=True,
                notification_state_file=Path(tmp) / "sent.json",
            ))

        self.assertEqual(result["status"], "data_unavailable")
        self.assertEqual(result["source_status"]["channel"]["kind"], "dns_resolution_failed")
        self.assertEqual(result["source_status"]["channel"]["scope"], "runtime_environment")
        self.assertIn("DNS/外网通道异常", result["empty_reason"])


if __name__ == "__main__":
    unittest.main()
