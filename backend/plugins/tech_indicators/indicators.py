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
    """指数移动平均序列（通达信口径：前 period 根 SMA 作种子，之后递推）。

    G1 修正：原实现用 vals[0] 作种子，导致 MACD DIF/DEA 与通达信偏差最多 5%。
    返回序列长度 = len(values) - period + 1（前 period 根合并为一个 SMA 种子）。
    """
    vals = _closes(values)
    if len(vals) < period:
        return []
    k = 2.0 / (period + 1)
    seed = sum(vals[:period]) / period  # 前 period 根 SMA 作起点
    out = [seed]
    for v in vals[period:]:
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
    """MACD：DIF/DEA/柱。返回 {dif, dea, histogram, golden, dead}，数据不足返回 None。

    G1 对齐修正：ema_series 改为 SMA 种子后序列变短（len-period+1），
    dif 取 ema_fast 与 ema_slow 对齐段，dea 与 dif 对齐到最后 N 个时间点。
    """
    vals = _closes(closes)
    if len(vals) < slow + signal:
        return None
    ema_fast = ema_series(vals, fast)  # 长度 len(vals)-fast+1
    ema_slow = ema_series(vals, slow)  # 长度 len(vals)-slow+1
    # dif：ema_fast 与 ema_slow 对齐（从 ema_fast 末尾对齐到 ema_slow）
    offset = len(ema_fast) - len(ema_slow)
    dif_series = [ema_fast[offset + i] - ema_slow[i] for i in range(len(ema_slow))]
    dea_series = ema_series(dif_series, signal)  # 长度 len(dif_series)-signal+1
    # dif 与 dea 对齐到最后 len(dea_series) 个时间点
    if len(dea_series) < 2 or len(dif_series) < len(dea_series) + 1:
        return None
    dif_aligned = dif_series[len(dif_series) - len(dea_series):]
    dif, dea = dif_aligned[-1], dea_series[-1]
    golden = dif_aligned[-2] < dea_series[-2] and dif_aligned[-1] >= dea_series[-1]
    dead = dif_aligned[-2] > dea_series[-2] and dif_aligned[-1] <= dea_series[-1]
    return {
        "dif": round(dif, 4), "dea": round(dea, 4),
        "histogram": round(2 * (dif - dea), 4),
        "golden": golden, "dead": dead,
        "dif_series": [round(x, 4) for x in dif_aligned[-20:]],
        "dea_series": [round(x, 4) for x in dea_series[-20:]],
    }


def _sma_recursive(rsv_series: list, period: int, seed: float = 50.0) -> list:
    """KDJ 的递推平滑（通达信口径：初始 K/D = seed）。

    G2 修正：原实现用 rsv[0] 作起点，与通达信前几根偏差大；现统一 seed=50.0。
    K[i] = (rsv[i] + (period-1) * K[i-1]) / period，即 K = SMA(RSV, period, 1)。
    """
    if not rsv_series:
        return []
    out = []
    prev = float(seed)
    for v in rsv_series:
        prev = (v + (period - 1) * prev) / period
        out.append(prev)
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
    """RSI（Wilder's smoothing 口径，对齐通达信/同花顺）。

    修复：原实现用简单平均 SMA，与市面软件口径不一致；现改用 Wilder 递推。
    返回 {rsi, rsi_prev, oversold_rebound}，数据不足返回 None。
    """
    vals = _closes(closes)
    if len(vals) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(vals)):
        change = vals[i] - vals[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    def _rsi_series(gains, losses):
        """Wilder's smoothing：前 period 根 SMA 作种子，之后 (prev×(period-1)+cur)/period 递推。"""
        if len(gains) < period:
            return []
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        out = [100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)]
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            out.append(100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss))
        return out

    rsis = _rsi_series(gains, losses)
    if not rsis:
        return None
    rsi = rsis[-1]
    rsi_prev = rsis[-2] if len(rsis) >= 2 else None
    oversold_rebound = rsi < oversold and rsi_prev is not None and rsi > rsi_prev
    return {"rsi": round(rsi, 2), "rsi_prev": round(rsi_prev, 2) if rsi_prev is not None else None,
            "oversold_rebound": oversold_rebound}


def compute_boll(closes: list, period: int = 20, k: float = 2.0) -> Optional[dict]:
    """BOLL：中轨/上轨/下轨 + 下轨反弹。

    G3 修正：反弹条件改为「昨触下轨 + 今高于昨 + 今站上5日均线」
    （原"昨触下轨今穿中轨"在主板几乎不触发）。
    """
    vals = _closes(closes)
    if len(vals) < period + 5:
        return None
    mb = sum(vals[-period:]) / period
    std = _std(vals[-period:]) or 0.0
    ub, lb = mb + k * std, mb - k * std
    ma5 = sum(vals[-5:]) / 5
    # G3：昨触下轨（昨收≤下轨）+ 今高于昨 + 今站上5日均线
    touched = vals[-2] <= lb
    higher = vals[-1] > vals[-2]
    above_ma5 = vals[-1] > ma5
    rebound = touched and higher and above_ma5
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


# ==== 详情副图全序列（长度与输入 closes 对齐，暖机期填 None，供 ECharts 直接绘制）====

def ma_series_full(closes: list) -> dict:
    """MA5/10/20/60 全序列（长度 = len(closes)，前 period-1 根为 None）。"""
    vals = _closes(closes)
    L = len(vals)
    out = {"ma5": [None] * L, "ma10": [None] * L, "ma20": [None] * L, "ma60": [None] * L}
    for period, key in ((5, "ma5"), (10, "ma10"), (20, "ma20"), (60, "ma60")):
        if L < period:
            continue
        for i in range(period - 1, L):
            out[key][i] = round(sum(vals[i - period + 1:i + 1]) / period, 4)
    return out


def macd_series_full(closes: list, fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[dict]:
    """MACD 全序列（dif/dea/hist 长度 = len(closes)，暖机期 None）。数据不足返回 None。"""
    vals = _closes(closes)
    L = len(vals)
    if L < slow + signal:
        return None
    ema_fast = ema_series(vals, fast)  # len L-fast+1，index m -> closes[m+fast-1]
    ema_slow = ema_series(vals, slow)  # len L-slow+1，index j -> closes[j+slow-1]
    offset = len(ema_fast) - len(ema_slow)  # slow - fast
    dif_full = [ema_fast[offset + j] - ema_slow[j] for j in range(len(ema_slow))]  # dif_full[m] -> closes[m+slow-1]
    dea_full = ema_series(dif_full, signal)  # dea_full[k] -> closes[k+slow+signal-2]
    hist_full = [2 * (dif_full[signal - 1 + k] - dea_full[k]) for k in range(len(dea_full))]

    def _pad(start, series):
        return [None] * start + series

    dif = _pad(slow - 1, dif_full)
    dea = _pad(slow + signal - 2, dea_full)
    hist = _pad(slow + signal - 2, hist_full)
    return {
        "dif": [round(x, 4) if x is not None else None for x in dif],
        "dea": [round(x, 4) if x is not None else None for x in dea],
        "hist": [round(x, 4) if x is not None else None for x in hist],
    }


def kdj_series_full(closes: list, highs: list, lows: list, n: int = 9, m1: int = 3, m2: int = 3) -> Optional[dict]:
    """KDJ 全序列（k/d/j 长度 = len(closes)，前 n-1 根用部分窗口，seed=50 口径）。"""
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
    j_series = [3 * kk - 2 * dd for kk, dd in zip(k_series, d_series)]
    return {
        "k": [round(x, 2) for x in k_series],
        "d": [round(x, 2) for x in d_series],
        "j": [round(x, 2) for x in j_series],
    }


def rsi_series_full(closes: list, period: int = 14) -> Optional[list]:
    """RSI 全序列（Wilder's smoothing，长度 = len(closes)，前 period 根为 None）。"""
    vals = _closes(closes)
    if len(vals) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(vals)):
        change = vals[i] - vals[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def _rsi(ag, al):
        return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)

    rsis = [None] * period + [_rsi(avg_gain, avg_loss)]
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsis.append(_rsi(avg_gain, avg_loss))
    return [round(x, 2) if x is not None else None for x in rsis]


def boll_series_full(closes: list, period: int = 20, k: float = 2.0) -> Optional[dict]:
    """BOLL 全序列（mb/ub/lb 长度 = len(closes)，前 period-1 根为 None）。"""
    vals = _closes(closes)
    L = len(vals)
    if L < period:
        return None
    mb, ub, lb = [None] * L, [None] * L, [None] * L
    for i in range(period - 1, L):
        window = vals[i - period + 1:i + 1]
        m = sum(window) / period
        std = _std(window) or 0.0
        mb[i] = round(m, 4)
        ub[i] = round(m + k * std, 4)
        lb[i] = round(m - k * std, 4)
    return {"mb": mb, "ub": ub, "lb": lb}
