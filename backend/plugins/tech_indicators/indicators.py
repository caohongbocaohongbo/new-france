"""18 经典技术指标选股指标纯函数（MACD/KDJ/RSI/BOLL，无前视）。"""
import math
from typing import Optional, Tuple


def _float(value, default=None):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def _closes(values) -> list:
    out = []
    for v in values or []:
        f = _float(v)
        if f is not None and f > 0:
            out.append(f)
    return out


def ema_series(values: list, period: int) -> list:
    """指数移动平均序列。values 至少 1 个元素。"""
    vals = _closes(values)
    if not vals:
        return []
    k = 2.0 / (period + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def sma_series(values: list, period: int) -> list:
    """简单移动平均序列（窗口内均值）。"""
    vals = _closes(values)
    if len(vals) < period:
        return []
    return [sum(vals[i - period + 1:i + 1]) / period for i in range(period - 1, len(vals))]


def _std(values: list):
    vals = _closes(values)
    if not vals:
        return None
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    return math.sqrt(var)


def compute_macd(closes: list, fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[dict]:
    """MACD：DIF/DEA/柱。返回 {dif, dea, histogram, golden, dead}，数据不足返回 None。"""
    vals = _closes(closes)
    if len(vals) < slow + signal:
        return None
    ema_fast = ema_series(vals, fast)
    ema_slow = ema_series(vals, slow)
    dif_series = [f - s for f, s in zip(ema_fast, ema_slow)]
    dea_series = ema_series(dif_series, signal)
    if len(dif_series) < 2 or len(dea_series) < 2:
        return None
    dif, dea = dif_series[-1], dea_series[-1]
    golden = dif_series[-2] < dea_series[-2] and dif_series[-1] >= dea_series[-1]
    dead = dif_series[-2] > dea_series[-2] and dif_series[-1] <= dea_series[-1]
    return {
        "dif": round(dif, 4), "dea": round(dea, 4),
        "histogram": round(2 * (dif - dea), 4),
        "golden": golden, "dead": dead,
        "dif_series": [round(x, 4) for x in dif_series[-20:]],
        "dea_series": [round(x, 4) for x in dea_series[-20:]],
    }


def _sma_recursive(rsv_series: list, period: int, weight: float = 1.0) -> list:
    """KDJ 的递推平滑：out[i] = (weight*rsv[i] + (period-weight)*out[i-1]) / period。"""
    if not rsv_series:
        return []
    out = [rsv_series[0]]
    for v in rsv_series[1:]:
        out.append((weight * v + (period - weight) * out[-1]) / period)
    return out


def compute_kdj(closes: list, highs: list, lows: list, n: int = 9, m1: int = 3, m2: int = 3,
                low_threshold: float = 20.0) -> Optional[dict]:
    """KDJ：K/D/J。返回 {k, d, j, low_golden}，数据不足返回 None。"""
    c, h, l = _closes(closes), _closes(highs), _closes(lows)
    if len(c) < n or len(h) < n or len(l) < n:
        return None
    rsv_series = []
    for i in range(len(c)):
        start = max(0, i - n + 1)
        hh = max(h[start:i + 1])
        ll = min(l[start:i + 1])
        rsv_series.append(50.0 if hh <= ll else (c[i] - ll) / (hh - ll) * 100)
    k_series = _sma_recursive(rsv_series, m1)
    d_series = _sma_recursive(k_series, m2)
    if len(k_series) < 2 or len(d_series) < 2:
        return None
    k, d = k_series[-1], d_series[-1]
    j = 3 * k - 2 * d
    low_golden = k_series[-2] < d_series[-2] and k_series[-1] >= d_series[-1] and k < low_threshold
    return {
        "k": round(k, 2), "d": round(d, 2), "j": round(j, 2),
        "low_golden": low_golden,
        "k_series": [round(x, 2) for x in k_series[-20:]],
        "d_series": [round(x, 2) for x in d_series[-20:]],
    }


def compute_rsi(closes: list, period: int = 14, oversold: float = 30.0) -> Optional[dict]:
    """RSI。返回 {rsi, rsi_prev, oversold_rebound}，数据不足返回 None。"""
    vals = _closes(closes)
    if len(vals) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(vals)):
        change = vals[i] - vals[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    def _rsi(gains, losses):
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)
    rsi = _rsi(gains, losses)
    rsi_prev = _rsi(gains[:-1], losses[:-1]) if len(gains) >= period else None
    oversold_rebound = rsi < oversold and rsi_prev is not None and rsi > rsi_prev
    return {"rsi": round(rsi, 2), "rsi_prev": round(rsi_prev, 2) if rsi_prev is not None else None,
            "oversold_rebound": oversold_rebound}


def compute_boll(closes: list, period: int = 20, k: float = 2.0) -> Optional[dict]:
    """BOLL：中轨/上轨/下轨 + 下轨反弹。数据不足返回 None。"""
    vals = _closes(closes)
    if len(vals) < period:
        return None
    mb = sum(vals[-period:]) / period
    std = _std(vals[-period:]) or 0.0
    ub, lb = mb + k * std, mb - k * std
    mb_prev = sum(vals[-period - 1:-1]) / period if len(vals) >= period + 1 else None
    std_prev = _std(vals[-period - 1:-1]) or 0.0 if len(vals) >= period + 1 else 0.0
    lb_prev = (mb_prev - k * std_prev) if mb_prev is not None else None
    rebound = lb_prev is not None and vals[-2] <= lb_prev and vals[-1] > mb
    return {"mb": round(mb, 4), "ub": round(ub, 4), "lb": round(lb, 4), "rebound": rebound}


def compute_tech_score(hit_count: int, macd: dict = None, kdj: dict = None,
                       rsi: dict = None, boll: dict = None) -> float:
    """综合分 0-100：命中数 × 25 + 强度加权（各 0-5）。"""
    score = min(4, hit_count) * 25.0
    if macd and macd.get("golden"):
        score += min(5.0, abs(macd.get("histogram") or 0) * 2)
    if kdj and kdj.get("low_golden"):
        score += min(5.0, (20 - (kdj.get("k") or 20)) / 4)
    if rsi and rsi.get("oversold_rebound"):
        score += min(5.0, (30 - (rsi.get("rsi") or 30)) / 6)
    if boll and boll.get("rebound"):
        score += 3.0
    return round(min(100.0, max(0.0, score)), 2)
