"""情绪周期指标纯函数（离线可测）。"""
import math


def _float(value, default=0.0):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def _clamp(value, low=0.0, high=100.0):
    return max(low, min(high, value))


def parse_zt_records(zt_pool) -> list:
    """把 zt_pool(DataFrame 或 list[dict]) 统一为涨停记录 list。"""
    if zt_pool is None:
        return []
    if hasattr(zt_pool, "to_dict"):
        raw = zt_pool.to_dict("records")
    else:
        raw = list(zt_pool)
    records = []
    for r in raw or []:
        code = str(r.get("代码") or r.get("code") or "").zfill(6)
        if not code or code == "000000":
            continue
        height = int(_float(r.get("连板数") or r.get("height"), 1))
        records.append({
            "code": code,
            "name": str(r.get("名称") or r.get("name") or ""),
            "height": max(1, height),
            "break_count": int(_float(r.get("炸板次数") or r.get("break_count"), 0)),
            "seal_time": int(_float(r.get("封板时间") or r.get("seal_time"), 0)),
            "industry": str(r.get("所属行业") or r.get("industry") or ""),
            "change_pct": _float(r.get("涨跌幅") or r.get("change_pct"), 0),
        })
    return records


def ladder_stats(records: list) -> dict:
    """连板梯队：首板/2板/3板/4板+ 家数、最高板、龙头。"""
    if not records:
        return {"first": 0, "second": 0, "third": 0, "higher": 0, "max_height": 0, "counts": {}, "leaders": []}
    heights = [r["height"] for r in records]
    max_height = max(heights)
    counts = {}
    for h in heights:
        counts[h] = counts.get(h, 0) + 1
    return {
        "first": counts.get(1, 0),
        "second": counts.get(2, 0),
        "third": counts.get(3, 0),
        "higher": sum(c for h, c in counts.items() if h >= 4),
        "max_height": max_height,
        "counts": counts,
        "leaders": [r for r in records if r["height"] == max_height][:10],
    }


def break_rate(records: list) -> float:
    """炸板率 = 炸板次数>0 的涨停股 / 全部涨停股。"""
    if not records:
        return 0.0
    return sum(1 for r in records if r["break_count"] > 0) / len(records)


def promotion_rate(today_heights: dict, prev_heights: dict):
    """晋级率 = 昨日 N 板股今日晋级 N+1 板的比例（无前视）。"""
    if not prev_heights:
        return None
    promoted = sum(1 for code, h in prev_heights.items() if today_heights.get(code) is not None and today_heights[code] >= h + 1)
    return promoted / len(prev_heights)


def compute_emotion(records: list, prev_heights: dict = None, index_gain: float = 0.0,
                    weights: dict = None, prev_score: float = None) -> dict:
    """情绪分与 regime。各因子归一化到 0-100，加权求和。"""
    weights = weights or {"zt_count": 20, "max_height": 20, "promotion": 20, "survival": 20, "premium": 10, "index": 10}
    if not records:
        return {"score": None, "regime": "no_data", "metrics": {}, "action": "空仓观望", "position": "0 成"}
    total = len(records)
    ladder = ladder_stats(records)
    max_height = ladder["max_height"]
    br = break_rate(records)
    today_heights = {r["code"]: r["height"] for r in records}
    promo = promotion_rate(today_heights, prev_heights or {})

    factors = {
        "zt_count": _clamp(total * 100.0 / 80.0),
        "max_height": _clamp(max_height * 100.0 / 7.0),
        "promotion": _clamp(promo * 100.0) if promo is not None else 50.0,
        "survival": _clamp((1.0 - br) * 100.0),
        "premium": 50.0,  # 溢价需次日行情，首次运行无数据取中性
        "index": _clamp((index_gain + 3.0) / 6.0 * 100.0),
    }
    score = round(sum(factors[k] * weights.get(k, 0) / 100.0 for k in factors), 2)
    regime = classify_regime(score, prev_score)
    metrics = {
        "zt_count": total,
        "max_height": max_height,
        "break_rate": round(br * 100, 2),
        "promotion_rate": round(promo * 100, 2) if promo is not None else None,
        "index_gain": round(index_gain, 2),
        "ladder": ladder,
        "factors": {k: round(v, 2) for k, v in factors.items()},
    }
    return {"score": score, "regime": regime, "metrics": metrics, "action": _action(regime), "position": _position(regime)}


def classify_regime(score, prev_score=None):
    """冰点/修复/发酵/高潮 + 退潮（高分回落）。"""
    if score is None:
        return "no_data"
    if prev_score is not None and prev_score >= 60 and prev_score - score >= 20:
        return "退潮"
    if score < 25:
        return "冰点"
    if score < 45:
        return "修复"
    if score < 65:
        return "发酵"
    return "高潮"


def _action(regime: str) -> str:
    return {"冰点": "空仓观望", "修复": "低吸", "发酵": "打首板", "高潮": "做龙头", "退潮": "空仓/减仓"}.get(regime, "观望")


def _position(regime: str) -> str:
    return {"冰点": "0-1 成", "修复": "1-3 成", "发酵": "3-5 成", "高潮": "5-7 成", "退潮": "0-2 成"}.get(regime, "0 成")
