"""26 条件盯盘表达式求值器单测。"""
import pytest

from backend.plugins.conditional_watch.expr import build_values, evaluate


def test_simple_compare():
    assert evaluate("price >= 10", {"price": 12.0}) is True
    assert evaluate("price >= 10", {"price": 8.0}) is False


def test_and_condition():
    assert evaluate("price >= 10 and change_percent >= 1", {"price": 12.0, "change_percent": 2.0}) is True
    assert evaluate("price >= 10 and change_percent >= 1", {"price": 12.0, "change_percent": 0.5}) is False


def test_or_condition():
    assert evaluate("price >= 100 or change_percent <= -5", {"price": 12.0, "change_percent": -6.0}) is True
    assert evaluate("price >= 100 or change_percent <= -5", {"price": 12.0, "change_percent": 1.0}) is False


def test_parens():
    assert evaluate("(price >= 10 or change_percent >= 1) and turnover_rate < 5",
                    {"price": 8.0, "change_percent": 2.0, "turnover_rate": 3.0}) is True


def test_missing_var_false():
    assert evaluate("price >= 10", {}) is False


def test_build_values_merge():
    values = build_values({"最新价": 10.5, "涨跌幅": 1.2, "换手率": 3.4}, {"threshold": 9.0})
    assert values["price"] == 10.5
    assert values["change_percent"] == 1.2
    assert values["turnover_rate"] == 3.4
    assert values["threshold"] == 9.0


def test_no_eval_injection():
    # 表达式里出现危险字符应安全返回，不执行任何代码
    assert evaluate("__import__('os').system('echo hacked')", {}) is False
    assert evaluate("price >= 10 or __import__('os')", {"price": 1.0}) is False
