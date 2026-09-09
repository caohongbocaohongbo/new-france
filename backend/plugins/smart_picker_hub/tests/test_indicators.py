"""22 聚合纯函数单测（离线，不联网）。"""
from backend.plugins.smart_picker_hub.indicators import (
    apply_gates, compute_hub_score, explain_chip, explain_pattern, explain_tech, explain_trend,
    extract_rows, fill_strategy_pct, filter_items, normalize_weights, percentiles, union_table, zcode,
)


def _tech_row(code="600001", score=70.0, pct=None, **extra):
    r = {"code": code, "name": "测试股份", "price": 10.0, "tech_score": score,
         "macd_golden": 1, "macd_dif": -0.12, "macd_dea": -0.10, "kdj_k": 15.0, "kdj_d": 20.0,
         "rsi": 25.0, "boll_mb": 9.5, "multi_hit": 1}
    if pct is not None:
        r["tech_score_pct"] = pct
    r.update(extra)
    return r


def _trend_row(code="600001", score=80.0, pct=None, **extra):
    r = {"code": code, "name": "测试股份", "price": 11.0, "ma_aligned": 1, "new_high": 1,
         "ma5": 11.0, "ma10": 10.0, "ma20": 9.0, "ma60": 8.0, "prev_high_60": 10.5,
         "high_break_pct": 0.05, "trend_score": score}
    if pct is not None:
        r["trend_score_pct"] = pct
    r.update(extra)
    return r


def test_zcode():
    assert zcode("1") == "000001"
    assert zcode(None) == "000000"


def test_extract_rows_dedup():
    snap = {"status": "completed",
            "items": [_tech_row(), _tech_row("600002", 60.0)],
            "golden_pool": [_tech_row()],  # 与 items 重复 → 去重
            "oversold_pool": [_tech_row("600003", 50.0)]}
    rows = extract_rows(snap)
    codes = [r["code"] for r in rows]
    assert codes == ["600001", "600002", "600003"]


def test_extract_rows_not_completed():
    assert extract_rows({"status": "no_data"}) == []
    assert extract_rows(None) == []


def test_fill_strategy_pct():
    rows = [_tech_row("600001", 70.0), _tech_row("600002", 60.0), _tech_row("600003", 70.0)]
    fill_strategy_pct(rows, "tech")
    assert rows[0]["tech_score_pct"] == rows[2]["tech_score_pct"]  # 并列分同 pct
    assert rows[1]["tech_score_pct"] < rows[0]["tech_score_pct"]
    # 已有 pct 的不覆盖
    rows = [_tech_row("600001", 70.0, pct=88.1)]
    fill_strategy_pct(rows, "tech")
    assert rows[0]["tech_score_pct"] == 88.1


def test_union_table_cross_strategy():
    merged = union_table({
        "tech": [_tech_row()],
        "trend": [_trend_row()],
        "pattern": [{"code": "600001", "pattern_score": 75.0, "pattern_score_pct": 60.0, "platform_break": 1}],
        "chip": [],
    })
    assert len(merged) == 1
    it = merged[0]
    assert it["code"] == "600001"
    assert it["hit_strategies"] == 3
    assert it["hit_flags"] == {"tech": 1, "trend": 1, "pattern": 1, "chip": 0}
    assert set(it["pct"]) == {"tech", "trend", "pattern"}
    assert set(it["hits"]) == {"tech", "trend", "pattern"}


def test_normalize_weights():
    w = normalize_weights({"tech": 0.3, "trend": 0.2, "pattern": 0.2, "chip": 0.3},
                          {"tech": True, "trend": True, "pattern": False, "chip": False})
    assert set(w) == {"tech", "trend"}
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert normalize_weights({"tech": 0.3}, {"tech": False}) == {}


def test_compute_hub_score():
    # 权重归一后：88.1 与 50.0 各半 = 69.05
    score = compute_hub_score({"tech": 88.1, "trend": 50.0}, {"tech": 0.5, "trend": 0.5})
    assert abs(score - 69.05) < 1e-9
    assert compute_hub_score({}, {"tech": 1.0}) == 0.0
    assert compute_hub_score({"tech": None}, {"tech": 1.0}) == 0.0


def test_percentiles():
    assert percentiles([70, 60, 70])[0] == 100.0
    assert percentiles([70, 60, 70])[1] == 33.3
    assert percentiles([]) == []


def test_apply_gates_zt_and_market():
    items = [{"code": "600001", "name": "A", "total_amount": 1e8},
             {"code": "600002", "name": "B", "total_amount": 1e8},
             {"code": "300001", "name": "C", "total_amount": 1e8},
             {"code": "688001", "name": "D", "total_amount": 1e8},
             {"code": "600003", "name": "ST股", "total_amount": 1e8}]
    out, applied, reason = apply_gates(items, {"600002"}, {"show_gem": False, "show_star": False, "min_amount": 0})
    assert applied and reason is None
    assert [i["code"] for i in out] == ["600001"]  # 涨停剔除 + 创业板/科创/ST 剔除
    # zt_pool 不可用 → 跳过涨停门控并显式标注
    out, applied, reason = apply_gates(items, None, {"show_gem": False, "show_star": False, "min_amount": 0})
    assert not applied and reason == "zt_pool_unavailable"
    assert [i["code"] for i in out] == ["600001", "600002"]
    # show_gem 打开 → 创业板纳入
    out, _, _ = apply_gates(items, None, {"show_gem": True, "show_star": False, "min_amount": 0})
    assert "300001" in [i["code"] for i in out]


def test_filter_items():
    items = [
        {"code": "600001", "name": "贵州茅台", "hub_score": 70.0, "hit_strategies": 1, "resonance": 0,
         "hit_flags": {"tech": 1, "trend": 0, "pattern": 0, "chip": 0}, "hits": {}},
        {"code": "600002", "name": "平安银行", "hub_score": 90.0, "hit_strategies": 2, "resonance": 1,
         "hit_flags": {"tech": 1, "trend": 1, "pattern": 0, "chip": 0}, "hits": {}},
        {"code": "300001", "name": "创业板票", "hub_score": 99.0, "hit_strategies": 3, "resonance": 1,
         "hit_flags": {"tech": 1, "trend": 1, "pattern": 1, "chip": 0}, "hits": {}},
        {"code": "600003", "name": "空分票", "hub_score": None, "hit_strategies": 1, "resonance": 0,
         "hit_flags": {"tech": 0, "trend": 0, "pattern": 0, "chip": 1}, "hits": {}},
    ]
    r = filter_items(items, limit=2, offset=0)
    assert [i["code"] for i in r["page"]] == ["600002", "600001"]  # 默认 main 剔除 300；hub_score 降序
    assert r["all"][-1]["code"] == "600003"  # None 分恒排最后
    r = filter_items(items, pool="resonance")
    assert [i["code"] for i in r["page"]] == ["600002"]
    r = filter_items(items, pool="tech")
    assert [i["code"] for i in r["page"]] == ["600002", "600001"]
    r = filter_items(items, min_hit=2)
    assert [i["code"] for i in r["page"]] == ["600002"]
    r = filter_items(items, q="平安")
    assert [i["code"] for i in r["page"]] == ["600002"]
    r = filter_items(items, market="gem")
    assert [i["code"] for i in r["page"]][0] == "300001"
    r = filter_items(items, sort="code", order="asc", limit=10)
    assert [i["code"] for i in r["page"]][0] == "600001"
    r = filter_items(items, sort="hub_score", order="asc")
    assert [i["code"] for i in r["page"]][0] == "600003"


def test_explainers():
    assert explain_tech(_tech_row())
    assert explain_trend(_trend_row())
    assert explain_pattern({"platform_break": 1, "platform_high": 10.0, "volume_ratio": 2.1})
    assert explain_chip({"concentrated": 1, "concentration_ratio": 0.08, "high_profit": 1,
                         "profit_ratio": 0.75, "trend_up": 1, "ma20_slope": 0.01})
    assert explain_tech({"macd_golden": 0, "kdj_golden": 0, "rsi_oversold": 0, "boll_rebound": 0}) is None
