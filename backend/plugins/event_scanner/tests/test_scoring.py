"""26 事件驱动雷达打分单测。"""
import pytest

from backend.plugins.event_scanner.scoring import classify_score


def test_merge_acquisition():
    r = classify_score("重大资产重组预案公布")
    assert r["event_type"] == "重组并购"
    assert r["impact_score"] == 5


def test_insider_buy():
    r = classify_score("控股股东增持股份")
    assert r["event_type"] == "增持回购"
    assert r["impact_score"] == 4


def test_holder_reduce():
    r = classify_score("大股东减持计划")
    assert r["event_type"] == "减持套现"
    assert r["impact_score"] == -4


def test_report_upgrade():
    r = classify_score("券商上调目标价至 20 元，买入评级")
    assert r["event_type"] == "研报上调"
    assert r["impact_score"] == 3


def test_report_downgrade():
    r = classify_score("下调评级至卖出")
    assert r["event_type"] == "研报下调"
    assert r["impact_score"] == -3


def test_unlock():
    r = classify_score("限售股解禁提示")
    assert r["event_type"] == "限售解禁"
    assert r["impact_score"] == -2


def test_unknown():
    r = classify_score("公司召开股东大会")
    assert r["event_type"] == "其他"
    assert r["impact_score"] == 0


def test_strongest_wins():
    # 同时命中重组(+5)和增持(+4)，取绝对值最大
    r = classify_score("重大资产重组暨控股股东增持")
    assert r["event_type"] == "重组并购"
    assert r["impact_score"] == 5
