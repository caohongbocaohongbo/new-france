import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.services import national_team_service
from backend.db.database import Base
from backend.db.models import NationalTeamEvent, NationalTeamHolding


BEIJING_TZ = timezone(timedelta(hours=8))


class NationalTeamServiceTest(unittest.TestCase):
    def test_detect_changes_marks_new_increase_decrease_and_unchanged(self):
        previous = [
            {"stock_code": "000001", "shareholder_name": "中国证券金融股份有限公司", "holding_type": "top10", "shares": 100.0},
            {"stock_code": "000002", "shareholder_name": "中国证券金融股份有限公司", "holding_type": "top10", "shares": 200.0},
            {"stock_code": "000003", "shareholder_name": "中国证券金融股份有限公司", "holding_type": "top10", "shares": 300.0},
            {"stock_code": "000004", "shareholder_name": "中国证券金融股份有限公司", "holding_type": "top10", "shares": 400.0},
        ]
        current = [
            {"stock_code": "000001", "shareholder_name": "中国证券金融股份有限公司", "holding_type": "top10", "shares": 110.0},
            {"stock_code": "000002", "shareholder_name": "中国证券金融股份有限公司", "holding_type": "top10", "shares": 190.0},
            {"stock_code": "000003", "shareholder_name": "中国证券金融股份有限公司", "holding_type": "top10", "shares": 300.0},
            {"stock_code": "000005", "shareholder_name": "中国证券金融股份有限公司", "holding_type": "top10", "shares": 500.0},
        ]

        changes = national_team_service.detect_changes("csf", previous, current)
        by_code = {item["stock_code"]: item["change_type"] for item in changes}

        self.assertEqual(by_code["000001"], "increase")
        self.assertEqual(by_code["000002"], "decrease")
        self.assertEqual(by_code["000003"], "unchanged")
        self.assertEqual(by_code["000005"], "new")
        self.assertEqual(by_code["000004"], "exit")

    def test_detect_changes_can_disable_exit_for_partial_latest_fetch(self):
        previous = [
            {"entity_key": "csf", "stock_code": "000004", "shareholder_name": "中国证券金融股份有限公司", "holding_type": "top10", "shares": 400.0},
        ]
        current = []

        changes = national_team_service.detect_changes("csf", previous, current, include_exits=False)

        self.assertEqual(changes, [])

    def test_select_latest_current_holdings_keeps_newest_report_period_per_identity(self):
        current = [
            {
                "entity_key": "csf",
                "stock_code": "000001",
                "shareholder_name": "中国证券金融股份有限公司",
                "holding_type": "top10",
                "report_period": "2026-03-31",
                "notice_date": "2026-04-30",
                "shares": 100.0,
            },
            {
                "entity_key": "csf",
                "stock_code": "000001",
                "shareholder_name": "中国证券金融股份有限公司",
                "holding_type": "top10",
                "report_period": "2026-06-30",
                "notice_date": "2026-08-30",
                "shares": 120.0,
            },
        ]

        latest = national_team_service.select_latest_current_holdings(current)

        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["report_period"], "2026-06-30")
        self.assertEqual(latest[0]["shares"], 120.0)

    def test_build_summary_counts_entities_and_latest_event(self):
        holdings = [
            {"entity_key": "huijin", "report_period": "2026-03-31", "notice_date": "2026-04-30"},
            {"entity_key": "huijin", "report_period": "2026-03-31", "notice_date": "2026-04-29"},
            {"entity_key": "csf", "report_period": "2026-05-11", "notice_date": "2026-05-16"},
        ]
        changes = [
            {"entity_key": "huijin", "change_type": "new"},
            {"entity_key": "huijin", "change_type": "increase"},
            {"entity_key": "csf", "change_type": "decrease"},
        ]
        events = [
            {
                "entity_key": "huijin",
                "event_date": "2026-05-01",
                "title": "中央汇金持仓披露更新",
                "source": "东方财富股东分析",
            }
        ]
        generated_at = datetime(2026, 6, 1, 15, 10, tzinfo=BEIJING_TZ)

        summary = national_team_service.build_summary_payload(
            holdings,
            changes,
            events,
            generated_at=generated_at,
            source_status={"ok": True, "source": "东方财富股东分析"},
        )

        huijin = next(item for item in summary["entities"] if item["entity_key"] == "huijin")
        self.assertEqual(summary["total_holdings"], 3)
        self.assertEqual(huijin["holding_count"], 2)
        self.assertEqual(huijin["latest_report_period"], "2026-03-31")
        self.assertEqual(huijin["change_counts"]["new"], 1)
        self.assertEqual(huijin["latest_event"]["title"], "中央汇金持仓披露更新")
        self.assertEqual(summary["source_status"]["source"], "东方财富股东分析")

    def test_filter_rows_to_latest_snapshot_window_uses_recent_refresh_batch(self):
        snapshot_time = datetime(2026, 6, 1, 15, 10, tzinfo=BEIJING_TZ)
        rows = [
            {
                "entity_key": "huijin",
                "report_period": "2026-03-31",
                "updated_at": (snapshot_time - timedelta(seconds=10)).isoformat(),
            },
            {
                "entity_key": "csf",
                "report_period": "2025-12-31",
                "updated_at": (snapshot_time - timedelta(hours=2)).isoformat(),
            },
        ]
        snapshot = {
            "fetched_at": snapshot_time,
            "matched_count": 1,
            "report_period": "2026-03-31",
        }

        filtered = national_team_service.filter_rows_to_latest_snapshot_window(rows, snapshot)

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["entity_key"], "huijin")

    def test_source_status_marks_partial_snapshot_unavailable_for_judgement(self):
        row = type("Snapshot", (), {
            "status": "partial",
            "source": "东方财富股东分析",
            "fetched_at": datetime(2026, 6, 1, 15, 10, tzinfo=BEIJING_TZ),
            "error_message": "部分过滤条件失败",
        })()

        status = national_team_service._source_status_from_snapshot(row)

        self.assertFalse(status["ok"])
        self.assertEqual(status["status"], "partial")
        self.assertIn("部分过滤条件失败", status["error"])

    def test_first_snapshot_event_is_labeled_as_first_record_not_disclosed_new_entry(self):
        item = {
            "entity_key": "huijin",
            "entity_name": "中央汇金",
            "stock_code": "601398",
            "stock_name": "工商银行",
            "shareholder_name": "中央汇金投资有限责任公司",
            "holding_type_name": "十大股东",
            "change_type": "new",
            "change_name": "不变",
            "notice_date": "2026-04-30",
        }

        event = national_team_service._event_from_change(item, "2026-03-31")

        self.assertIn("首次记录", event["title"])
        self.assertNotIn("新进", event["title"])
        self.assertIn("中央汇金投资有限责任公司", event["title"])
        self.assertIn("不等同于上市公司披露的新进", event["summary"])

    def test_dedupe_event_changes_collapses_top10_and_free_float_duplicate(self):
        changes = [
            {
                "entity_key": "social_security",
                "entity_name": "社保基金",
                "stock_code": "603871",
                "stock_name": "嘉友国际",
                "shareholder_name": "汇添富基金管理股份有限公司-社保基金16032组合",
                "holding_type": "top10",
                "holding_type_name": "十大股东",
                "change_type": "new",
                "change_name": "不变",
                "notice_date": "2026-05-29",
            },
            {
                "entity_key": "social_security",
                "entity_name": "社保基金",
                "stock_code": "603871",
                "stock_name": "嘉友国际",
                "shareholder_name": "汇添富基金管理股份有限公司-社保基金16032组合",
                "holding_type": "free_float_top10",
                "holding_type_name": "十大流通股东",
                "change_type": "new",
                "change_name": "不变",
                "notice_date": "2026-05-29",
            },
        ]

        deduped = national_team_service._dedupe_event_changes(changes)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["stock_code"], "603871")

    def test_list_events_returns_offset_page_with_traceable_source_urls(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = testing_session()
        try:
            for idx in range(10):
                db.add(NationalTeamEvent(
                    entity_key="huijin",
                    entity_name="中央汇金",
                    event_date=f"2026-05-{30 - idx:02d}",
                    title=f"中央汇金事件 {idx}",
                    summary=f"公开披露摘要 {idx}",
                    related_stock_code=f"600{idx:03d}",
                    related_stock_name=f"测试股票{idx}",
                    source="东方财富股东分析",
                    source_url="https://data.eastmoney.com/gdfx/HoldingAnalyse.html",
                    fetched_at=datetime(2026, 6, 1, 10, idx, tzinfo=BEIJING_TZ),
                ))
            db.commit()
        finally:
            db.close()

        with patch.object(national_team_service, "SessionLocal", testing_session):
            first = national_team_service.list_events(entity="huijin", limit=5, offset=0)
            second = national_team_service.list_events(entity="huijin", limit=5, offset=5)

        self.assertEqual(first["total"], 10)
        self.assertEqual(first["limit"], 5)
        self.assertEqual(first["offset"], 0)
        self.assertEqual(second["limit"], 5)
        self.assertEqual(second["offset"], 5)
        self.assertEqual(len(first["items"]), 5)
        self.assertEqual(len(second["items"]), 5)
        self.assertTrue(all(item["source_url"] for item in first["items"] + second["items"]))
        self.assertTrue(set(item["title"] for item in first["items"]).isdisjoint(
            item["title"] for item in second["items"]
        ))

    def test_get_top_holder_stats_returns_three_entities_from_real_holdings(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = testing_session()
        try:
            for idx in range(11):
                db.add(NationalTeamHolding(
                    entity_key="huijin",
                    entity_name="中央汇金",
                    shareholder_name=f"中央汇金测试股东{idx}",
                    stock_code=f"600{idx:03d}",
                    stock_name=f"汇金股票{idx}",
                    report_period="2026-05-31",
                    notice_date="2026-06-01",
                    shares=1000000 + idx,
                    share_ratio=1 + idx / 10,
                    market_value=100000000 + idx * 1000000,
                    holder_rank=idx + 1,
                    holding_type="top10",
                    holding_type_name="十大股东",
                    source="东方财富股东分析",
                    source_url="https://data.eastmoney.com/gdfx/HoldingAnalyse.html",
                ))
            db.add(NationalTeamHolding(
                entity_key="huijin",
                entity_name="中央汇金",
                shareholder_name="中央汇金流通股东",
                stock_code="601999",
                stock_name="流通口径",
                report_period="2026-05-31",
                notice_date="2026-06-01",
                shares=9000000,
                market_value=9999999999,
                holder_rank=1,
                holding_type="free_float_top10",
                holding_type_name="十大流通股东",
                source="东方财富股东分析",
                source_url="https://data.eastmoney.com/gdfx/HoldingAnalyse.html",
            ))
            db.add(NationalTeamHolding(
                entity_key="huijin",
                entity_name="中央汇金",
                shareholder_name="中央汇金测试股东10",
                stock_code="600010",
                stock_name="汇金股票10旧报告期",
                report_period="2025-12-31",
                notice_date="2026-03-31",
                shares=99000000,
                share_ratio=9.9,
                market_value=999999999999,
                holder_rank=1,
                holding_type="top10",
                holding_type_name="十大股东",
                source="东方财富股东分析",
                source_url="https://data.eastmoney.com/gdfx/HoldingAnalyse.html",
            ))
            for key, name in [("social_security", "社保基金"), ("csf", "证金公司")]:
                db.add(NationalTeamHolding(
                    entity_key=key,
                    entity_name=name,
                    shareholder_name=f"{name}测试组合",
                    stock_code="000001",
                    stock_name=f"{name}股票",
                    report_period="2026-05-31",
                    notice_date="2026-06-01",
                    shares=3000000,
                    share_ratio=2.5,
                    market_value=300000000,
                    holder_rank=2,
                    holding_type="top10",
                    holding_type_name="十大股东",
                    source="东方财富股东分析",
                    source_url="https://data.eastmoney.com/gdfx/HoldingAnalyse.html",
                ))
            db.commit()
        finally:
            db.close()

        with patch.object(national_team_service, "SessionLocal", testing_session):
            stats = national_team_service.get_top_holder_stats(limit=10)

        self.assertEqual(stats["limit"], 10)
        self.assertEqual(len(stats["entities"]), 3)
        huijin = next(item for item in stats["entities"] if item["entity_key"] == "huijin")
        self.assertEqual(len(huijin["items"]), 10)
        self.assertEqual(huijin["items"][0]["stock_name"], "汇金股票10")
        self.assertNotIn("流通口径", [item["stock_name"] for item in huijin["items"]])
        self.assertNotIn("汇金股票10旧报告期", [item["stock_name"] for item in huijin["items"]])
        self.assertTrue(all(item["source_url"] for item in huijin["items"]))
        self.assertGreater(huijin["items"][0]["market_value"], huijin["items"][-1]["market_value"])

    def test_get_capital_flow_stats_sorts_buy_and_sell_by_disclosed_amount(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = testing_session()
        try:
            base_fields = {
                "entity_key": "huijin",
                "entity_name": "中央汇金",
                "shareholder_name": "中央汇金投资有限责任公司",
                "report_period": "2026-03-31",
                "notice_date": "2026-04-30",
                "shares": 1000.0,
                "share_ratio": 1.0,
                "holder_rank": 1,
                "holding_type": "top10",
                "holding_type_name": "十大股东",
                "source": "东方财富股东分析",
                "source_url": "https://data.eastmoney.com/gdfx/HoldingAnalyse.html",
            }
            rows = [
                ("600001", "买入高金额", 20.0, 2000.0),
                ("600002", "买入低金额", 5.0, 1000.0),
                ("600003", "卖出高金额", -30.0, 3000.0),
                ("600004", "卖出低金额", -10.0, 1000.0),
                ("600005", "不变股票", 0.0, 5000.0),
                ("600006", "缺少市值", 9.0, None),
            ]
            for code, name, shares_change, market_value in rows:
                db.add(NationalTeamHolding(
                    **base_fields,
                    stock_code=code,
                    stock_name=name,
                    shares_change=shares_change,
                    change_name="增加" if shares_change > 0 else ("减少" if shares_change < 0 else "不变"),
                    market_value=market_value,
                ))
            free_float_fields = {
                **base_fields,
                "holding_type": "free_float_top10",
                "holding_type_name": "十大流通股东",
            }
            db.add(NationalTeamHolding(
                **free_float_fields,
                stock_code="600001",
                stock_name="买入高金额流通口径",
                shares_change=20.0,
                market_value=999999.0,
            ))
            for key, name in [("social_security", "社保基金"), ("csf", "证金公司")]:
                db.add(NationalTeamHolding(
                    entity_key=key,
                    entity_name=name,
                    shareholder_name=f"{name}测试组合",
                    stock_code="000001",
                    stock_name=f"{name}买入股票",
                    report_period="2026-03-31",
                    notice_date="2026-04-30",
                    shares=1000.0,
                    shares_change=10.0,
                    change_name="增加",
                    market_value=1000.0,
                    holder_rank=1,
                    holding_type="top10",
                    holding_type_name="十大股东",
                    source="东方财富股东分析",
                    source_url="https://data.eastmoney.com/gdfx/HoldingAnalyse.html",
                ))
            db.commit()
        finally:
            db.close()

        with patch.object(national_team_service, "SessionLocal", testing_session):
            stats = national_team_service.get_capital_flow_stats(limit=10)

        huijin = next(item for item in stats["entities"] if item["entity_key"] == "huijin")
        self.assertEqual([item["stock_name"] for item in huijin["buy"]["items"]], ["买入高金额", "买入低金额"])
        self.assertEqual([item["stock_name"] for item in huijin["sell"]["items"]], ["卖出高金额", "卖出低金额"])
        self.assertEqual(huijin["buy"]["items"][0]["direction"], "buy")
        self.assertEqual(huijin["sell"]["items"][0]["direction"], "sell")
        self.assertAlmostEqual(huijin["buy"]["items"][0]["change_amount"], 40.0)
        self.assertAlmostEqual(huijin["sell"]["items"][0]["change_amount"], 90.0)
        self.assertNotIn("买入高金额流通口径", [item["stock_name"] for item in huijin["buy"]["items"]])
        self.assertTrue(all(item["source_url"] for item in huijin["buy"]["items"] + huijin["sell"]["items"]))

    def test_get_holding_value_stats_aggregates_top_listed_companies(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = testing_session()
        try:
            base_fields = {
                "entity_key": "social_security",
                "entity_name": "社保基金",
                "report_period": "2026-03-31",
                "notice_date": "2026-04-30",
                "shares": 1000.0,
                "share_ratio": 1.0,
                "holder_rank": 1,
                "holding_type": "top10",
                "holding_type_name": "十大股东",
                "source": "东方财富股东分析",
                "source_url": "https://data.eastmoney.com/gdfx/HoldingAnalyse.html",
            }
            rows = [
                ("600001", "合并第一", "全国社保基金一一一组合", 400.0),
                ("600001", "合并第一", "全国社保基金一一二组合", 300.0),
                ("600002", "第二公司", "全国社保基金一一三组合", 500.0),
                ("600003", "第三公司", "全国社保基金一一四组合", 200.0),
            ]
            for code, name, shareholder, market_value in rows:
                db.add(NationalTeamHolding(
                    **base_fields,
                    stock_code=code,
                    stock_name=name,
                    shareholder_name=shareholder,
                    market_value=market_value,
                ))
            free_float_fields = {
                **base_fields,
                "holding_type": "free_float_top10",
                "holding_type_name": "十大流通股东",
            }
            db.add(NationalTeamHolding(
                **free_float_fields,
                stock_code="600001",
                stock_name="合并第一流通口径",
                shareholder_name="全国社保基金一一一组合",
                market_value=9999.0,
            ))
            for key, name, shareholder in [
                ("huijin", "中央汇金", "中央汇金投资有限责任公司"),
                ("csf", "证金公司", "中国证券金融股份有限公司"),
            ]:
                db.add(NationalTeamHolding(
                    entity_key=key,
                    entity_name=name,
                    shareholder_name=shareholder,
                    stock_code="000001",
                    stock_name=f"{name}第一公司",
                    report_period="2026-03-31",
                    notice_date="2026-04-30",
                    shares=1000.0,
                    market_value=100.0,
                    holder_rank=1,
                    holding_type="top10",
                    holding_type_name="十大股东",
                    source="东方财富股东分析",
                    source_url="https://data.eastmoney.com/gdfx/HoldingAnalyse.html",
                ))
            db.commit()
        finally:
            db.close()

        with patch.object(national_team_service, "SessionLocal", testing_session):
            stats = national_team_service.get_holding_value_stats(limit=10)

        social_security = next(item for item in stats["entities"] if item["entity_key"] == "social_security")
        self.assertEqual(social_security["full_name"], "全国社会保障基金理事会")
        self.assertEqual(social_security["english_name"], "National Council for Social Security Fund")
        self.assertEqual([item["stock_name"] for item in social_security["items"]], ["合并第一", "第二公司", "第三公司"])
        self.assertAlmostEqual(social_security["items"][0]["market_value"], 700.0)
        self.assertEqual(social_security["items"][0]["holder_count"], 2)
        self.assertEqual(social_security["items"][0]["holding_type_name"], "十大股东")
        self.assertTrue(all(item["source_url"] for item in social_security["items"]))


if __name__ == "__main__":
    unittest.main()
