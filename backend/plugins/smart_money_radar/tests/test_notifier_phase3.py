from datetime import datetime, timezone, timedelta

from backend.plugins.smart_money_radar.notifier import build_payload


def test_notifier_groups_priority_stages_and_scores():
    now = datetime(2026, 8, 18, 10, 30, tzinfo=timezone(timedelta(hours=8)))
    subject, text, html = build_payload([
        {"code": "600001", "name": "A", "stage": "启动前夕", "smart_money_score": 88, "launch_score": 90, "price_impact": 0.1},
        {"code": "600002", "name": "B", "stage": "潜伏", "smart_money_score": 70, "launch_score": 30, "price_impact": 0.5},
    ], "local", now)
    assert "SmartMoney=88" in text
    assert text.index("启动前夕") < text.index("潜伏")
    assert "Launch" in html and "背离度" in html


def test_notifier_emits_one_group_header_per_sorted_stage():
    now = datetime(2026, 8, 18, 10, 30, tzinfo=timezone(timedelta(hours=8)))
    _, text, html = build_payload([
        {"code": "600003", "name": "C", "stage": "潜伏", "smart_money_score": 60, "launch_score": 20},
        {"code": "600001", "name": "A", "stage": "启动前夕", "smart_money_score": 88, "launch_score": 90},
        {"code": "600002", "name": "B", "stage": "潜伏", "smart_money_score": 70, "launch_score": 30},
    ], "local", now)

    assert text.count("【启动前夕】") == 1
    assert text.count("【潜伏】") == 1
    assert html.count("<th colspan='10'") == 2
