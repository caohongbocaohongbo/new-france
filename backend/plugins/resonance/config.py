"""四维共振信号配置。"""
import os
from datetime import timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[3]
REPORT_DIR = PROJECT_DIR / "reports"
DATA_DIR = PROJECT_DIR / "data"
BEIJING_TZ = timezone(timedelta(hours=8))
SNAPSHOT_NAME = "resonance"


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _env_bool(name, default):
    return str(os.environ.get(name, str(default))).strip().lower() in {"1", "true", "yes", "on"}


CONFIG = {
    # 信号灯基准阈值（只读建议，仅 RESONANCE_* env 显式修改，运行时禁止自动变更）
    "red_threshold": _env_float("RESONANCE_RED_THRESHOLD", 75),
    "green_threshold": _env_float("RESONANCE_GREEN_THRESHOLD", 30),
    # K 线
    "kline_workers": int(_env_float("RESONANCE_KLINE_WORKERS", 20)),
    # 展示层过滤（默认仅主板）
    "show_gem": _env_bool("RESONANCE_SHOW_GEM", False),
    "show_star": _env_bool("RESONANCE_SHOW_STAR", False),
}
