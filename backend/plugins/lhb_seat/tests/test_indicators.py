"""02 龙虎榜席位归一化与画像单测（离线）。"""
from backend.plugins.lhb_seat.indicators import (
    build_seat_profile, classify_seat_type, match_confidence, normalize_seat_name,
)
from backend.plugins.lhb_seat.service import normalize_seats, aggregate_seat_stats


def test_normalize_seat_name():
    a = normalize_seat_name("国泰君安证券股份有限公司上海江苏路证券营业部")
    b = normalize_seat_name("国泰君安 上海江苏路 证券营业部")
    assert a == b  # 同营业部不同写法合并
    assert "证券营业部" not in a


def test_normalize_strips_parens():
    assert normalize_seat_name("某证券营业部(原某某)") == "某"


def test_classify_seat_type():
    assert classify_seat_type("机构专用") == "机构"
    assert classify_seat_type("沪股通专用") == "沪股通"
    assert classify_seat_type("深股通专用") == "深股通"
    assert classify_seat_type("某量化私募") == "量化"
    assert classify_seat_type("中国银河证券绍兴证券营业部") == "游资"
    assert classify_seat_type("某某普通营业部") == "普通"


def test_match_confidence_bounds():
    assert 0 <= match_confidence("国泰君安上海江苏路证券营业部") <= 1
    assert match_confidence("某(原)") < 1.0


def test_build_seat_profile_no_lookahead_returns():
    samples = [
        {"ret_t1": 2.0, "ret_t3": 5.0, "board_type": "首板", "theme": "AI"},
        {"ret_t1": -1.0, "ret_t3": None, "board_type": "首板", "theme": "AI"},
        {"ret_t1": 1.0, "ret_t3": 2.0, "board_type": "连板", "theme": "芯片"},
    ]
    p = build_seat_profile(samples)
    assert p["sample_count"] == 3
    assert p["win_rate_t1"] == round(2 / 3, 4)
    assert p["prefer_board"] == "首板"
    assert p["prefer_theme"] == "AI"


def test_build_seat_profile_empty():
    assert build_seat_profile([]) == {}


def test_normalize_seats_and_aggregate():
    seats = normalize_seats([
        {"seat_name": "国泰君安上海江苏路证券营业部", "side": "buy", "amount": 1e8},
        {"seat_name": "国泰君安 上海江苏路 证券营业部", "side": "buy", "amount": 2e8},
        {"seat_name": "机构专用", "side": "buy", "amount": 3e8},
    ])
    stats = aggregate_seat_stats(seats)
    assert stats[0]["normalized_name"] == "国泰君安上海江苏路"  # 合并为同一席位
    assert stats[0]["appearances"] == 2
    assert stats[0]["known_label"] == "章盟主"
