"""28 低位竞价雷达纯函数单测。"""
import pytest

from backend.plugins.auction_radar import factors, pool, quality, risk, scoring
from backend.plugins.auction_radar.models import AuctionQuote


def test_auction_turnover_unit_lot_to_pct():
    # 100手=10000股；流通股本100万股 -> 换手 1%，不是 0.01%
    assert factors.auction_turnover_pct(10000, 1000000) == pytest.approx(1.0)


def test_auction_gap_sign():
    assert factors.auction_gap(12.34, 11.84) == pytest.approx(4.2229, abs=1e-3)
    assert factors.auction_gap(10, 0) is None


def test_match_amount_yuan():
    assert factors.match_amount_yuan(10, 1000) == 10000.0
    assert factors.match_amount_yuan(0, 100) is None


def test_orderbook_imbalance_normalized():
    assert factors.orderbook_imbalance(100, 100) == 0.0
    assert factors.orderbook_imbalance(150, 50) == pytest.approx(0.5)
    assert factors.orderbook_imbalance(None, 50) is None


def test_orderbook_pressure_decay_no_withdraw_word():
    frames = [{"bid_vol_total": 1000, "price": 10.0}, {"bid_vol_total": 400, "price": 9.8}]
    assert factors.orderbook_pressure_decay(frames) is True
    # 只描述现象，不宣称撤单：不涨不降不应触发
    frames2 = [{"bid_vol_total": 1000, "price": 10.0}, {"bid_vol_total": 1200, "price": 10.1}]
    assert factors.orderbook_pressure_decay(frames2) is False


def test_gap_band_pass():
    assert factors.gap_band_pass(5) == 100.0
    assert factors.gap_band_pass(10) < 100.0
    assert factors.gap_band_pass(-3) < 100.0


def test_weighted_score_coverage_gate():
    score, cov = scoring.weighted_auction_score({"gap": 80}, {"gap": 1.0}, min_coverage=0.7)
    assert score == 80.0 and cov == 1.0
    score, cov = scoring.weighted_auction_score(
        {"gap": 80}, {"gap": 0.5, "missing": 0.5}, min_coverage=0.7)
    assert score is None and cov == 0.5


def test_total_score_low_appears_once():
    # 0.4*70 + 0.6*80 = 76.0
    assert scoring.total_score(70, 80, 0.0, 0.4, 0.6) == 76.0
    assert scoring.total_score(200, 200, 0.0, 0.4, 0.6) == 100.0  # clamp


def test_build_ranking_tiebreak():
    items = [{"code": "b", "total_score": 5}, {"code": "a", "total_score": 5}, {"code": "c", "total_score": 9}]
    ranked = scoring.build_ranking(items)
    assert ranked[0]["code"] == "c" and ranked[0]["rank"] == 1
    assert ranked[1]["code"] == "a" and ranked[2]["code"] == "b"


def test_quality_invalid_code():
    q = AuctionQuote(trade_date="2026-10-09", code="abc")
    r = quality.validate_quote(q, "2026-10-09", "PRE_AUCTION")
    assert r.quality == "INVALID"


def test_quality_nonpositive_price():
    q = AuctionQuote(trade_date="2026-10-09", code="600000",
                     indicative_price_yuan=0.0, prev_close_yuan=10.0)
    r = quality.validate_quote(q, "2026-10-09", "PRE_AUCTION")
    assert r.quality == "PARTIAL"


def test_hard_exclude_st_and_board():
    assert risk.hard_exclude({"code": "600001", "name": "*ST测试"}, "VALID")[0] is True
    assert risk.hard_exclude({"code": "300001", "name": "创业"}, "VALID")[0] is True
    assert risk.hard_exclude({"code": "688001", "name": "科创"}, "VALID")[0] is True
    assert risk.hard_exclude({"code": "600000", "name": "主板"}, "VALID")[0] is False


def test_pool_hash_deterministic_and_norm():
    items = [{"code": "600000", "low_score": 10}, {"code": "000001", "low_score": 20}]
    assert pool.hash_pool(items) == pool.hash_pool(list(reversed(items)))
    norm = pool._norm_items([{"code": "600000"}, {"code": 600000}])
    assert len(norm) == 1 and norm[0]["code"] == "600000"


def test_pressure_decay_penalty():
    penalty, tags = risk.pressure_decay_penalty(
        [{"bid_vol_total": 1000, "price": 10.0}, {"bid_vol_total": 300, "price": 9.5}])
    assert penalty > 0 and "挂单压力衰减" in tags
