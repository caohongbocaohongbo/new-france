"""20 形态突破选股指标纯函数（平台突破 + 缺口不回补，无前视）。"""
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


def is_narrow_platform(highs: list, lows: list, platform_days: int = 20,
                       abs_threshold: float = 0.10, converge_threshold: float = 0.7) -> Tuple[bool, Optional[float]]:
    """窄幅平台（G1 修正：绝对振幅小 OR 近期收敛）。返回 (命中, 平台上沿)。

    G1 深挖修正：原 `platform_range/platform_days < avg_daily_range × threshold`
    对发散票（highs/lows 反向移动）误判——max-min/20 相对 avg_daily_range 总是偏小，
    导致发散也命中。改为两个更稳健的判定（任一命中即窄幅）：
      1. 绝对振幅小：platform_range（max-min）/ mid < abs_threshold（恒定窄幅）；
      2. 近期收敛：近 platform_days 日日均振幅 < 近 60 日日均振幅 × converge_threshold。
    覆盖：恒定窄幅（命中）、近期收敛（命中）、高波动（不命中）、发散（不命中）。
    """
    h, l = _closes(highs), _closes(lows)
    if len(h) < 60 or len(l) < 60:
        return False, None
    platform_high = max(h[-platform_days:])
    platform_low = min(l[-platform_days:])
    mid = (platform_high + platform_low) / 2
    if mid <= 0:
        return False, None
    platform_range = (platform_high - platform_low) / mid  # 平台累计振幅（比例）
    # 判定 1：绝对振幅小（恒定窄幅）
    abs_narrow = platform_range < abs_threshold
    # 判定 2：近期收敛（近 platform_days 日日均振幅 < 近 60 日日均振幅 × threshold）
    recent_ranges = [(hh - ll) / ((hh + ll) / 2) for hh, ll in zip(h[-platform_days:], l[-platform_days:]) if (hh + ll) > 0]
    all_ranges = [(hh - ll) / ((hh + ll) / 2) for hh, ll in zip(h[-60:], l[-60:]) if (hh + ll) > 0]
    recent_avg = sum(recent_ranges) / len(recent_ranges) if recent_ranges else 1.0
    all_avg = sum(all_ranges) / len(all_ranges) if all_ranges else 1.0
    converge = recent_avg < all_avg * converge_threshold
    return abs_narrow or converge, platform_high


def is_platform_breakout(closes: list, vols: list, platform_high: float,
                         vol_ratio_threshold: float = 2.0) -> Tuple[bool, Optional[float]]:
    """放量突破平台上沿。返回 (命中, 量比)。"""
    c = _closes(closes)
    v = _closes(vols)
    if not c or platform_high is None:
        return False, None
    vr = v[-1] / (sum(v[-6:-1]) / 5) if v and len(v) >= 6 and sum(v[-6:-1]) > 0 else 1.0
    return c[-1] > platform_high and vr > vol_ratio_threshold, round(vr, 2)


def detect_gap_hold(opens: list, highs: list, lows: list, vols: list, platform_high: float,
                    lookback: int = 3, min_gap_pct: float = 0.02) -> Optional[dict]:
    """跳空缺口不回补。返回 {gap_pct, hold, gap_type, gap_low, volume_ratio}，无缺口返回 None。

    G3 索引语义：lookback=3 表示3日前缺口。
    gap_idx = -(lookback+1) = -4，表示3日前K线（倒数第4根）。
    prev_high = highs[-5]，表示4日前最高价（缺口下沿）。
    lows[-3:] 检查近3日最低价是否都 > 缺口下沿（不回补）。
    G2：breakaway 三条件 = gap_pct>2% AND volume_ratio>1.5 AND gap_open>platform_high。
    """
    o, h, l = _closes(opens), _closes(highs), _closes(lows)
    if len(o) < lookback + 2 or len(h) < lookback + 2 or len(l) < lookback + 1:
        return None
    gap_idx = -(lookback + 1)  # -4 = 3日前K线
    prev_high = h[gap_idx - 1]  # -5 = 4日前最高价（缺口下沿）
    gap_open = o[gap_idx]
    gap_pct = (gap_open - prev_high) / prev_high if prev_high > 0 else 0.0
    if gap_pct < min_gap_pct:
        return None
    # 不回补：近 lookback 日 min(low) > 缺口下沿
    recent_lows = l[gap_idx + 1:]
    hold = min(recent_lows) > prev_high if recent_lows else False
    # G2：breakaway 三条件
    v = _closes(vols)
    vr = v[-1] / (sum(v[-6:-1]) / 5) if v and len(v) >= 6 and sum(v[-6:-1]) > 0 else 1.0
    is_breakaway = (gap_pct > min_gap_pct and vr > 1.5
                    and platform_high is not None and gap_open > platform_high)
    return {
        "gap_pct": round(gap_pct, 4), "hold": bool(hold),
        "gap_type": "breakaway" if is_breakaway else "common",
        "gap_low": prev_high, "volume_ratio": round(vr, 2),
    }


def compute_pattern_score(platform_break: bool, gap_hold: bool, dual_hit: bool,
                          break_pct: float, gap_pct: float) -> float:
    """综合分 0-100：平台突破 40 + 缺口不回补 40 + 双形态共振 20（含幅度加权）。"""
    score = 0.0
    if platform_break:
        score += 40 + min(20.0, (_float(break_pct, 0) or 0) * 400)
    if gap_hold:
        score += 40 + min(20.0, (_float(gap_pct, 0) or 0) * 400)
    if dual_hit:
        score += 20.0
    return round(min(100.0, score), 2)
