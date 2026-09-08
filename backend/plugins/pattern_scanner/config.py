"""形态突破选股配置。"""
import os
from datetime import timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[3]
REPORT_DIR = PROJECT_DIR / "reports"
DATA_DIR = PROJECT_DIR / "data"
BEIJING_TZ = timezone(timedelta(hours=8))
SNAPSHOT_NAME = "pattern_scanner"


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _env_bool(name, default):
    return str(os.environ.get(name, str(default))).strip().lower() in {"1", "true", "yes", "on"}


CONFIG = {
    # 平台突破阈值
    "platform_days": int(_env_float("PATTERN_PLATFORM_DAYS", 20)),
    "abs_threshold": _env_float("PATTERN_ABS_THRESHOLD", 0.10),
    "converge_threshold": _env_float("PATTERN_CONVERGE_THRESHOLD", 0.7),
    "breakout_vol_ratio": _env_float("PATTERN_BREAKOUT_VOL_RATIO", 2.0),
    # 缺口阈值
    "gap_lookback": int(_env_float("PATTERN_GAP_LOOKBACK", 3)),
    "min_gap_pct": _env_float("PATTERN_MIN_GAP_PCT", 0.02),
    # 粗筛
    "min_amount": _env_float("PATTERN_MIN_AMOUNT", 5e7),
    # K 线
    "kline_days": int(_env_float("PATTERN_KLINE_DAYS", 130)),
    "kline_workers": int(_env_float("PATTERN_KLINE_WORKERS", 30)),
    # 展示层过滤（默认仅主板，PATTERN_* env 可开创业板/科创板）
    "show_gem": _env_bool("PATTERN_SHOW_GEM", False),
    "show_star": _env_bool("PATTERN_SHOW_STAR", False),
    # 双形态共振🔥邮件推送冷却（分钟，仿 15 RED 信号推送）
    "notify_cooldown_minutes": int(_env_float("PATTERN_NOTIFY_COOLDOWN_MINUTES", 30)),
}
