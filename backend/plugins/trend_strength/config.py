"""趋势强度选股配置。"""
import os
from datetime import timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[3]
REPORT_DIR = PROJECT_DIR / "reports"
DATA_DIR = PROJECT_DIR / "data"
BEIJING_TZ = timezone(timedelta(hours=8))
SNAPSHOT_NAME = "trend_strength"


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _env_bool(name, default):
    return str(os.environ.get(name, str(default))).strip().lower() in {"1", "true", "yes", "on"}


CONFIG = {
    # 命中阈值
    "ma_tol": _env_float("TREND_MA_TOL", 0.005),
    "new_high_window": int(_env_float("TREND_NEW_HIGH_WINDOW", 60)),
    "leader_volume_ratio": _env_float("TREND_LEADER_VOLUME_RATIO", 1.5),
    # 粗筛
    "min_amount": _env_float("TREND_MIN_AMOUNT", 5e7),
    # K 线
    "kline_days": int(_env_float("TREND_KLINE_DAYS", 130)),
    "kline_workers": int(_env_float("TREND_KLINE_WORKERS", 30)),
    # 展示层过滤（默认仅主板，TREND_* env 可开创业板/科创板）
    "show_gem": _env_bool("TREND_SHOW_GEM", False),
    "show_star": _env_bool("TREND_SHOW_STAR", False),
}
