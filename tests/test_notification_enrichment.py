import unittest
from datetime import date
from types import SimpleNamespace

import pandas as pd

from backend.agents.layer2_signal_engine.skills.base import SkillResult
from backend.agents.layer3_recommendation import notifier


def _stock():
    factors = {
        "pullback": SkillResult("回撤幅度", "pullback", 7.0, 0.15, "回撤4.5%", True),
        "pe": SkillResult("市盈率", "pe", 5.0, 0.08, "PE数据缺失", False),
    }
    return SimpleNamespace(
        code="002030",
        name="达安基因",
        zt_date="2026-05-11",
        ref_price=7.30,
        current_price=6.97,
        drop_pct=-4.52,
        adjusted_score=69,
        recommendation="BUY",
        rank=1,
        factor_scores=factors,
        extra={
            "price_history": [
                {"date": "2026-05-11", "close": 7.30, "drawdown_pct": 0.0, "change_pct": 10.0},
                {"date": "2026-05-25", "close": 6.97, "drawdown_pct": -4.52, "change_pct": 2.5},
            ]
        },
    )


class NotificationEnrichmentTest(unittest.TestCase):
    def test_html_recommendation_contains_price_history_and_failed_audit_details(self):
        stock = _stock()
        audit_results = {
            "stocks": [
                {
                    "code": "002030",
                    "downgraded": True,
                    "original_rec": "STRONG_BUY",
                    "adjusted_rec": "BUY",
                    "fail_count": 1,
                    "validations": [
                        {"name": "封板时间", "status": "fail", "detail": "封板时间为0，数据缺失"},
                        {"name": "价格校验", "status": "pass", "detail": "现价6.97在合理范围"},
                    ],
                }
            ]
        }

        html = notifier._html_recommendation_section(
            [stock], "BUY 建议买入", "#F39C12", audit_results
        )

        self.assertIn("价格/回撤走势", html)
        self.assertIn("2026-05-11", html)
        self.assertIn("6.97", html)
        self.assertIn("-4.52%", html)
        self.assertIn("未满足项", html)
        self.assertIn("封板时间", html)
        self.assertIn("封板时间为0，数据缺失", html)

    def test_html_recommendation_separates_screening_date_from_watch_dates(self):
        stock = _stock()

        html = notifier._html_recommendation_section(
            [stock], "BUY 建议买入", "#F39C12", {}, date(2026, 6, 8)
        )

        self.assertIn("筛选日期:2026-06-08", html)
        self.assertIn("加入关注:--", html)
        self.assertIn("涨停日:2026-05-11", html)

    def test_html_recommendation_explains_missing_history(self):
        stock = _stock()
        stock.extra = {}

        html = notifier._html_recommendation_section(
            [stock], "BUY 建议买入", "#F39C12", {}, date(2026, 6, 8)
        )

        self.assertIn("历史走势暂缺", html)

    def test_build_price_history_from_historical_uses_ref_price_drawdown(self):
        hist = pd.DataFrame(
            [
                {"日期": "2026-05-09", "收盘": 7.0, "涨跌幅": 1.0},
                {"日期": "2026-05-11", "收盘": 7.3, "涨跌幅": 10.0},
                {"日期": "2026-05-12", "收盘": 7.1, "涨跌幅": -2.74},
                {"日期": "2026-05-13", "收盘": 6.97, "涨跌幅": -1.83},
            ]
        )

        series = notifier._build_price_history_series(hist, "2026-05-11", 7.3)

        self.assertEqual([row["date"] for row in series], ["2026-05-11", "2026-05-12", "2026-05-13"])
        self.assertEqual(series[-1]["close"], 6.97)
        self.assertEqual(series[-1]["drawdown_pct"], -4.52)

    def test_zt_source_note_displays_raw_and_final_counts(self):
        zt_list = [{"code": "000001", "name": "平安银行", "price": 10.0}]
        meta = {"source": "eastmoney_direct", "raw_count": 64, "final_count": 46, "filtered_count": 18}

        html = notifier._html_zt_table(zt_list, "seal_time", meta)

        self.assertIn("数据源:eastmoney_direct", html)
        self.assertIn("原始64只", html)
        self.assertIn("展示46只", html)
        self.assertIn("过滤18只", html)

    def test_text_summary_labels_zt_count_as_pool_effective_count(self):
        zt_list = [{"code": "000001", "name": "平安银行", "price": 10.0}]
        meta = {"source": "eastmoney_direct", "raw_count": 1, "final_count": 1, "filtered_count": 0}

        text = notifier._build_text_content([], date(2026, 6, 1), 0, zt_list, 0, {}, meta, {})

        self.assertIn("今日涨停池有效数: 1 只", text)
        self.assertNotIn("今日新增涨停", text)

    def test_text_summary_keeps_zt_rows_when_national_team_section_exists(self):
        zt_list = [{"code": "000001", "name": "平安银行", "price": 10.0, "change_pct": 10.0}]
        national_team = {
            "source_status": {"ok": True, "source": "东方财富股东分析", "fetched_at": "2026-06-01T15:10:00+08:00"},
            "entities": [],
            "events": [],
        }

        text = notifier._build_text_content([], date(2026, 6, 1), 0, zt_list, 0, {}, {}, {}, national_team)

        self.assertLess(text.index("【今日涨停股列表】"), text.index("000001 平安银行"))
        self.assertLess(text.index("000001 平安银行"), text.index("【国家队动向】"))

    def test_national_team_summary_renders_source_and_no_fake_events(self):
        national_team = {
            "total_holdings": 2,
            "entities": [
                {
                    "entity_key": "huijin",
                    "entity_name": "中央汇金",
                    "holding_count": 1,
                    "latest_report_period": "2026-03-31",
                    "latest_notice_date": "2026-04-30",
                    "change_counts": {"new": 0, "increase": 1, "decrease": 0, "exit": 0, "unchanged": 0},
                    "latest_event": {"title": "中央汇金 增持 工商银行(601398)", "event_date": "2026-04-30"},
                },
                {
                    "entity_key": "csf",
                    "entity_name": "证金公司",
                    "holding_count": 1,
                    "latest_report_period": "2026-03-31",
                    "latest_notice_date": "2026-04-30",
                    "change_counts": {"new": 0, "increase": 0, "decrease": 0, "exit": 0, "unchanged": 1},
                    "latest_event": None,
                },
            ],
            "events": [
                {
                    "event_date": "2026-04-30",
                    "entity_name": "中央汇金",
                    "title": "中央汇金 增持 工商银行(601398)",
                    "source": "东方财富股东分析",
                    "source_url": "https://data.eastmoney.com/gdfx/HoldingAnalyse.html",
                }
            ],
            "source_status": {
                "ok": True,
                "source": "东方财富股东分析",
                "source_url": "https://data.eastmoney.com/gdfx/HoldingAnalyse.html",
                "fetched_at": "2026-06-01T15:10:00+08:00",
            },
        }

        text = notifier._build_text_content([], date(2026, 6, 1), 0, [], 0, {}, {}, {}, national_team)
        html = notifier._build_html_content([], date(2026, 6, 1), 0, [], 0, "seal_time", {}, {}, {}, national_team)

        self.assertIn("【国家队动向】", text)
        self.assertIn("中央汇金", text)
        self.assertIn("东方财富股东分析", text)
        self.assertIn("国家队动向", html)
        self.assertIn("中央汇金 增持 工商银行", html)
        self.assertIn("数据源", html)

    def test_text_content_splits_zt_pool_from_watchlist_recommendations(self):
        stock = _stock()
        stock.recommendation = "BUY"
        stock.extra["added_date"] = "2026-05-12"
        zt_list = [{"code": "000001", "name": "平安银行", "price": 10.0, "change_pct": 10.0}]

        text = notifier._build_text_content([stock], date(2026, 6, 1), 0, zt_list, 0, {}, {}, {})

        self.assertIn("【今日涨停股列表】", text)
        self.assertIn("【历史监控池回撤推荐】", text)
        self.assertIn("加入关注:2026-05-12", text)
        self.assertNotIn("【今日筛选结果】", text)

    def test_text_content_excludes_pass_stocks_from_recommendation_section(self):
        buy_stock = _stock()
        buy_stock.recommendation = "BUY"
        pass_stock = _stock()
        pass_stock.code = "000656"
        pass_stock.name = "金科股份"
        pass_stock.recommendation = "PASS"
        pass_stock.adjusted_score = 41

        text = notifier._build_text_content([buy_stock, pass_stock], date(2026, 6, 1), 0, [], 0, {}, {}, {})

        self.assertIn("达安基因(002030)", text)
        self.assertNotIn("金科股份(000656)", text)
        self.assertNotIn("[PASS]", text)

    def test_html_content_splits_zt_pool_from_watchlist_recommendations(self):
        stock = _stock()
        stock.recommendation = "WATCH"
        stock.extra["added_date"] = "2026-05-12"
        zt_list = [{"code": "000001", "name": "平安银行", "price": 10.0, "change_pct": 10.0}]

        html = notifier._build_html_content([stock], date(2026, 6, 1), 0, zt_list, 0, "seal_time", {}, {}, {})

        self.assertIn("今日涨停池列表", html)
        self.assertIn("历史监控池回撤推荐", html)
        self.assertIn("加入关注:2026-05-12", html)
        self.assertNotIn("今日筛选结果", html)

    def test_html_content_excludes_pass_stocks_from_recommendation_section(self):
        buy_stock = _stock()
        buy_stock.recommendation = "BUY"
        pass_stock = _stock()
        pass_stock.code = "000656"
        pass_stock.name = "金科股份"
        pass_stock.recommendation = "PASS"
        pass_stock.adjusted_score = 41

        html = notifier._build_html_content([buy_stock, pass_stock], date(2026, 6, 1), 0, [], 0, "seal_time", {}, {}, {})

        self.assertIn("达安基因", html)
        self.assertNotIn("金科股份", html)
        self.assertNotIn("PASS", html)


if __name__ == "__main__":
    unittest.main()
