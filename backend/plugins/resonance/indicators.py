"""15 四维共振信号指标纯函数（修复版：D2/D3 None 重分配 + D1 持续性 + 阈值只读）。"""
import math
from typing import Optional, Tuple


def _float(value, default=None):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def _clip(value, low=0.0, high=100.0):
    return max(low, min(high, value))


def _closes(values: list) -> list:
    out = []
    for v in values or []:
        f = _float(v)
        if f is not None and f > 0:
            out.append(f)
    return out


def _linear_slope(values: list) -> float:
    """最小二乘斜率，序列不足 2 个点返回 0。"""
    vals = [_float(v, 0) or 0 for v in values or []]
    n = len(vals)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(vals) / n
    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(vals))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den if den > 0 else 0.0


def d1_score(smart_ratio: float, state: str, continuity_rounds: int = 0) -> float:
    """D1 资金主力分：smart_ratio 归一化 + 状态修正 + 持续性加成。"""
    sr = _float(smart_ratio, 0) or 0
    base = (_clip(sr, -0.05, 0.05) + 0.05) / 0.10 * 100
    state_bonus = {"拉升": 15, "吸筹": 10, "观望": 0, "出货": -20}.get(state, 0)
    continuity_bonus = min(_float(continuity_rounds, 0) or 0, 10)
    return round(_clip(base + state_bonus + continuity_bonus), 2)


def d2_score(closes: list, highs: list = None) -> Optional[float]:
    """D2 价格动量分：数据不足(len<22)返回 None，由调用方做权重重分配。"""
    c = _closes(closes)
    h = _closes(highs) if highs else c
    if len(c) < 22 or len(h) < 20:
        return None
    last = c[-1]
    ret_5 = (last - c[-6]) / c[-6] if c[-6] > 0 else 0.0
    ret_20 = (last - c[-21]) / c[-21] if c[-21] > 0 else 0.0
    high20 = max(h[-20:])
    dist = (high20 - last) / high20 if high20 > 0 else 0.0
    s5 = (_clip(ret_5, -0.05, 0.05) + 0.05) / 0.10 * 100
    s20 = (_clip(ret_20, -0.10, 0.10) + 0.10) / 0.20 * 100
    sd = max(1.0 - dist / 0.15, 0.0) * 100
    return round(s5 * 0.35 + s20 * 0.40 + sd * 0.25, 2)


def d3_score(code: str, board_heat_json: dict, db_session=None) -> Tuple[Optional[float], bool, str]:
    """D3 板块共振分：两路匹配，均不可用返回 (None, True, "unavailable")。"""
    code = str(code).zfill(6)
    # 路径 1：快照 JSON 的 codes 字段（05 方案接口约定）
    if board_heat_json:
        items = board_heat_json.get("items") or []
        matched = [i.get("score", 0) for i in items if code in (i.get("codes") or [])]
        if matched:
            return round(_clip(max(matched)), 2), False, "snapshot"
    # 路径 2：board_stock_daily 表查询
    if db_session is not None:
        try:
            from backend.db.plugin_models import BoardDaily, BoardStockDaily
            row = (db_session.query(BoardDaily.score)
                   .join(BoardStockDaily, BoardDaily.board_code == BoardStockDaily.board_code)
                   .filter(BoardStockDaily.code == code)
                   .order_by(BoardDaily.score.desc())
                   .first())
            if row and row.score is not None:
                return round(_clip(float(row.score)), 2), False, "db"
        except Exception:  # noqa: BLE001
            pass
    return None, True, "unavailable"


def d4_score(vols: list) -> Tuple[float, float]:
    """D4 量能形态分（日线口径）：返回 (score, vol_ratio)。数据不足返回 (50.0, 1.0)。"""
    v = _closes(vols)
    if len(v) < 7:
        return 50.0, 1.0
    avg5 = sum(v[-6:-1]) / 5.0 if sum(v[-6:-1]) > 0 else 1.0
    vol_ratio = v[-1] / avg5 if avg5 > 0 else 1.0
    slope = _linear_slope(v[-5:])
    slope_norm = _clip((slope / avg5 + 1.0) / 2.0 * 100)
    ratio_score = (_clip(vol_ratio, 0.5, 2.5) - 0.5) / 2.0 * 100
    return round(ratio_score * 0.6 + slope_norm * 0.4, 2), round(vol_ratio, 4)


def compute_resonance(d1: float, d1_state: str, d2: Optional[float],
                      d3_result: tuple, d4_result: tuple,
                      red_threshold: float = 75.0, green_threshold: float = 30.0) -> dict:
    """修复版共振分合成：D2/D3 缺失时权重重分配，vol_ratio 不作信号硬门槛。"""
    d3_val, d3_degraded, d3_source = d3_result
    d4_val, vol_ratio = d4_result
    d2_insufficient = d2 is None

    if d3_degraded and d2_insufficient:
        score = d1 * 0.60 + d4_val * 0.40
        red_t, green_t = red_threshold + 5, green_threshold + 4
    elif d3_degraded:
        score = d1 * 0.45 + d2 * 0.30 + d4_val * 0.25
        red_t, green_t = red_threshold + 3, green_threshold + 2
    elif d2_insufficient:
        score = d1 * 0.47 + d3_val * 0.27 + d4_val * 0.27
        red_t, green_t = red_threshold + 1, green_threshold + 1
    else:
        score = d1 * 0.35 + d2 * 0.25 + d3_val * 0.20 + d4_val * 0.20
        red_t, green_t = red_threshold, green_threshold

    score = round(_clip(score), 2)
    # 信号灯：D1_state 参与（§2.4），vol_ratio 不参与（修复5）
    if score >= red_t and d1_state in {"吸筹", "拉升"}:
        signal = "RED"
    elif score <= green_t and d1_state == "出货":
        signal = "GREEN"
    else:
        signal = "YELLOW"

    return {
        "resonance_score": score, "signal": signal,
        "d3_degraded": d3_degraded, "d3_source": d3_source,
        "d2_insufficient": d2_insufficient,
        "vol_ratio": round(vol_ratio, 3),
        "red_threshold": red_t, "green_threshold": green_t,
    }
