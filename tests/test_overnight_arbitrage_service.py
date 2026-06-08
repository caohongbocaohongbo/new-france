import unittest
import asyncio
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from backend.services.overnight_arbitrage_service import (
    HISTORY_FILE,
    REPORT_FILE,
    build_overnight_decision,
    notify_overnight_decision,
    run_overnight_arbitrage,
    update_overnight_history,
    write_overnight_report,
)


class OvernightArbitrageServiceTest(unittest.TestCase):
    def test_build_decision_filters_non_target_stocks_and_ranks_buy_candidates(self):
        quotes = pd.DataFrame([
            {
                "代码": "600001", "名称": "主板强势", "最新价": 12.3, "涨跌幅": 9.82,
                "成交额": 420_000_000, "换手率": 7.2, "量比": 2.8, "流通市值": 8_800_000_000,
                "市盈率": 18.6, "最高价": 12.6,
            },
            {
                "代码": "000002", "名称": "主板观察", "最新价": 8.6, "涨跌幅": 7.25,
                "成交额": 180_000_000, "换手率": 3.8, "量比": 1.7, "流通市值": 6_500_000_000,
            },
            {
                "代码": "300003", "名称": "创业板强势", "最新价": 21.1, "涨跌幅": 13.2,
                "成交额": 900_000_000, "换手率": 12.1, "量比": 4.1, "流通市值": 7_500_000_000,
            },
            {
                "代码": "600004", "名称": "ST风险", "最新价": 4.1, "涨跌幅": 5.0,
                "成交额": 230_000_000, "换手率": 5.4, "量比": 2.1, "流通市值": 3_500_000_000,
            },
            {
                "代码": "000005", "名称": "缩量冲高", "最新价": 6.2, "涨跌幅": 8.9,
                "成交额": 25_000_000, "换手率": 1.2, "量比": 0.8, "流通市值": 2_900_000_000,
            },
        ])
        zt_pool = pd.DataFrame([
            {"代码": "600001", "封板时间": 144100, "炸板次数": 0, "连板数": 1},
            {"代码": "000002", "封板时间": 0, "炸板次数": 1, "连板数": 0},
        ])
        minute_strength = {
            "600001": {"last_15m_change_pct": 1.25, "last_close_position": 0.96, "source": "Yahoo 5m"},
            "000002": {"last_15m_change_pct": 0.28, "last_close_position": 0.72, "source": "Yahoo 5m"},
        }

        decision = build_overnight_decision(
            quotes,
            zt_pool=zt_pool,
            minute_strength=minute_strength,
            target_date=date(2026, 6, 4),
            generated_at="2026-06-04 14:43:00",
            limit=10,
        )

        self.assertEqual(decision["strategy"], "overnight_arbitrage")
        self.assertEqual(decision["date"], "2026-06-04")
        self.assertEqual(decision["valid_window"], "14:43-14:55")
        self.assertEqual(decision["buy_count"], 1)
        self.assertEqual(decision["watch_count"], 1)
        self.assertEqual([item["code"] for item in decision["results"]], ["600001", "000002"])
        self.assertEqual(decision["results"][0]["action"], "BUY")
        self.assertEqual(decision["results"][0]["pe"], 18.6)
        self.assertEqual(decision["results"][0]["intraday_pullback_pct"], -2.38)
        self.assertGreater(decision["results"][0]["decision_score"], decision["results"][1]["decision_score"])
        self.assertIn("尾盘承接", " ".join(decision["results"][0]["reasons"]))
        rejected = {item["code"]: item["reason"] for item in decision["rejected"]}
        self.assertEqual(rejected["300003"], "创业板已排除")
        self.assertEqual(rejected["600004"], "ST或退市风险已排除")
        self.assertEqual(rejected["000005"], "成交额不足")

    def test_build_decision_keeps_candidates_when_minute_source_fails(self):
        quotes = pd.DataFrame([{
            "代码": "600010", "名称": "无分时源", "最新价": 10.0, "涨跌幅": 9.6,
            "成交额": 330_000_000, "换手率": 6.1, "量比": 2.3, "流通市值": 7_000_000_000,
        }])
        zt_pool = pd.DataFrame([{"代码": "600010", "封板时间": 143800, "炸板次数": 0, "连板数": 1}])

        decision = build_overnight_decision(
            quotes,
            zt_pool=zt_pool,
            minute_strength={},
            target_date=date(2026, 6, 4),
            generated_at="2026-06-04 14:43:00",
            limit=10,
        )

        self.assertEqual(decision["buy_count"], 1)
        self.assertEqual(decision["results"][0]["code"], "600010")
        self.assertEqual(decision["source_status"]["yahoo_5m"]["status"], "optional_missing")
        self.assertIn("5分钟K线缺失", decision["results"][0]["risks"])

    def test_build_decision_only_excludes_chinext_not_other_a_share_boards(self):
        quotes = pd.DataFrame([{
            "代码": "688010", "名称": "科创强势", "最新价": 18.0, "涨跌幅": 9.2,
            "成交额": 360_000_000, "换手率": 6.8, "量比": 2.5, "流通市值": 9_000_000_000,
        }])

        decision = build_overnight_decision(
            quotes,
            zt_pool=pd.DataFrame(),
            minute_strength={},
            target_date=date(2026, 6, 4),
            generated_at="2026-06-04 14:43:00",
            limit=10,
        )

        self.assertEqual(decision["results"][0]["code"], "688010")
        self.assertNotIn("688010", {item["code"] for item in decision["rejected"]})

    def test_write_overnight_report_uses_independent_cache_file(self):
        payload = {"status": "completed", "strategy": "overnight_arbitrage", "results": []}
        original_text = REPORT_FILE.read_text(encoding="utf-8") if REPORT_FILE.exists() else None
        try:
            write_overnight_report(payload)
            self.assertTrue(REPORT_FILE.exists())
            self.assertIn("overnight_arbitrage", REPORT_FILE.read_text(encoding="utf-8"))
            self.assertTrue(str(REPORT_FILE).endswith("reports/overnight_arbitrage_latest.json"))
        finally:
            if original_text is None:
                REPORT_FILE.unlink(missing_ok=True)
            else:
                REPORT_FILE.write_text(original_text, encoding="utf-8")

    def test_history_dedupes_same_trading_day_and_aggregates_dimensions(self):
        with TemporaryDirectory() as tmp:
            history_file = Path(tmp) / "overnight_history.json"
            day1_first = {
                "date": "2026-06-04",
                "generated_at": "2026-06-04 14:35:00",
                "results": [{
                    "action": "BUY",
                    "code": "600001",
                    "name": "主板强势",
                    "current_price": 10.0,
                    "pe": 12.0,
                    "intraday_pullback_pct": -1.5,
                    "decision_score": 61.0,
                }],
            }
            day1_latest = {
                "date": "2026-06-04",
                "generated_at": "2026-06-04 14:43:00",
                "results": [{
                    "action": "WATCH",
                    "code": "600001",
                    "name": "主板强势",
                    "current_price": 10.5,
                    "pe": 13.0,
                    "intraday_pullback_pct": -0.5,
                    "decision_score": 58.0,
                }],
            }
            day2 = {
                "date": "2026-06-05",
                "generated_at": "2026-06-05 14:43:00",
                "results": [{
                    "action": "BUY",
                    "code": "600001",
                    "name": "主板强势",
                    "current_price": 9.5,
                    "pe": 11.0,
                    "intraday_pullback_pct": -2.0,
                    "decision_score": 66.0,
                }],
            }

            update_overnight_history(day1_first, history_file=history_file)
            update_overnight_history(day1_latest, history_file=history_file)
            history = update_overnight_history(day2, history_file=history_file)

        record = history["records"][0]
        self.assertEqual(record["code"], "600001")
        self.assertEqual(record["recommendation_count"], 2)
        self.assertEqual([item["date"] for item in record["recommendations"]], ["2026-06-04", "2026-06-05"])
        self.assertEqual(record["recommendations"][0]["action"], "WATCH")
        self.assertEqual(record["price_pushes"], [10.5, 9.5])
        self.assertEqual(record["pe_values"], [13.0, 11.0])
        self.assertEqual(record["pullback_values"], [-0.5, -2.0])
        self.assertEqual(record["metrics"]["price_avg"], 10.0)
        self.assertEqual(record["metrics"]["pe_avg"], 12.0)
        self.assertEqual(record["metrics"]["pullback_avg"], -1.25)

    def test_history_keeps_latest_same_day_when_older_run_replayed(self):
        with TemporaryDirectory() as tmp:
            history_file = Path(tmp) / "overnight_history.json"
            latest = {
                "date": "2026-06-04",
                "generated_at": "2026-06-04 14:43:00",
                "results": [{
                    "action": "BUY",
                    "code": "600001",
                    "name": "主板强势",
                    "current_price": 10.5,
                    "pe": 13.0,
                    "intraday_pullback_pct": -0.5,
                    "decision_score": 62.0,
                }],
            }
            older = {
                "date": "2026-06-04",
                "generated_at": "2026-06-04 14:35:00",
                "results": [{
                    "action": "WATCH",
                    "code": "600001",
                    "name": "主板强势",
                    "current_price": 10.0,
                    "pe": 12.0,
                    "intraday_pullback_pct": -1.5,
                    "decision_score": 58.0,
                }],
            }

            update_overnight_history(latest, history_file=history_file)
            history = update_overnight_history(older, history_file=history_file)

        record = history["records"][0]
        self.assertEqual(record["recommendation_count"], 1)
        self.assertEqual(record["recommendations"][0]["generated_at"], "2026-06-04 14:43:00")
        self.assertEqual(record["price_pushes"], [10.5])

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
            with patch("backend.services.overnight_arbitrage_service.notify_overnight_decision", return_value=True):
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

    def test_notify_uses_existing_email_sender(self):
        decision = {
            "date": "2026-06-04",
            "valid_window": "14:43-14:55",
            "buy_count": 1,
            "watch_count": 0,
            "results": [{
                "action": "BUY",
                "code": "600001",
                "name": "主板强势",
                "decision_score": 78.5,
                "change_pct": 9.8,
                "volume_ratio": 2.8,
                "risks": [],
            }],
        }
        notify_config = {
            "email_enabled": True,
            "email_to": "receiver@example.com",
            "email_user": "sender@example.com",
            "email_host": "smtp.example.com",
            "email_port": 587,
        }

        with patch("backend.agents.layer3_recommendation.notifier._get_notify_config", return_value=notify_config), \
                patch("backend.agents.layer3_recommendation.notifier._send_email", return_value=(True, "OK")) as send_email:
            ok = notify_overnight_decision(decision)

        self.assertTrue(ok)
        kwargs = send_email.call_args.kwargs
        self.assertIn("尾盘隔夜套利", kwargs["subject"])
        self.assertIn("600001", kwargs["text_content"])
        self.assertIn("600001", kwargs["html_content"])
        self.assertEqual(kwargs["notify_config"], notify_config)


if __name__ == "__main__":
    unittest.main()
