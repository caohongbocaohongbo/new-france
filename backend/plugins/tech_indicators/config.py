"""经典技术指标选股配置。"""
import os
from datetime import timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[3]
REPORT_DIR = PROJECT_DIR / "reports"
DATA_DIR = PROJECT_DIR / "data"
BEIJING_TZ = timezone(timedelta(hours=8))
SNAPSHOT_NAME = "tech_indicators"


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _env_bool(name, default):
    return str(os.environ.get(name, str(default))).strip().lower() in {"1", "true", "yes", "on"}


CONFIG = {
    # 命中阈值
    "kdj_low_threshold": _env_float("TECH_KDJ_LOW", 20.0),
    "rsi_oversold": _env_float("TECH_RSI_OVERSOLD", 30.0),
    # 粗筛
    "min_amount": _env_float("TECH_MIN_AMOUNT", 5e7),
    # K 线
    "kline_days": int(_env_float("TECH_KLINE_DAYS", 130)),
    "kline_workers": int(_env_float("TECH_KLINE_WORKERS", 30)),
    # 展示层过滤（默认仅主板，TECH_* env 可开创业板/科创板）
    "show_gem": _env_bool("TECH_SHOW_GEM", False),
    "show_star": _env_bool("TECH_SHOW_STAR", False),
}
