"""低位涨停选股器配置。"""
import os
from datetime import timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[3]
REPORT_DIR = PROJECT_DIR / "reports"
DATA_DIR = PROJECT_DIR / "data"
BEIJING_TZ = timezone(timedelta(hours=8))
SNAPSHOT_NAME = "low_position"


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _env_bool(name, default):
    return str(os.environ.get(name, str(default))).strip().lower() in {"1", "true", "yes", "on"}


CONFIG = {
    # 低位阈值（AND-OR 结构）
    "pullback_min": _env_float("LOW_POSITION_PULLBACK_MIN", -0.25),
    "percentile_max": _env_float("LOW_POSITION_PERCENTILE_MAX", 0.30),
    "ma_period": int(_env_float("LOW_POSITION_MA_PERIOD", 20)),
    "down_window": int(_env_float("LOW_POSITION_DOWN_WINDOW", 5)),
    # 粗筛阈值
    "min_amount": _env_float("LOW_POSITION_MIN_AMOUNT", 5e7),
    "mcap_min": _env_float("LOW_POSITION_MCAP_MIN", 2e9),
    "mcap_max": _env_float("LOW_POSITION_MCAP_MAX", 3e10),
    # 展示层过滤（默认仅主板，env 可打开创业板/科创板）
    "show_gem": _env_bool("LOW_POSITION_SHOW_GEM", False),
    "show_star": _env_bool("LOW_POSITION_SHOW_STAR", False),
    # K 线
    "kline_days": int(_env_float("LOW_POSITION_KLINE_DAYS", 130)),
    "kline_workers": int(_env_float("LOW_POSITION_KLINE_WORKERS", 30)),
    # 涨停历史
    "zt_lookback_days": int(_env_float("LOW_POSITION_ZT_LOOKBACK", 250)),
    # 综合分权重
    "weights": {
        "pullback": _env_float("LOW_POSITION_W_PULLBACK", 0.45),
        "percentile": _env_float("LOW_POSITION_W_PERCENTILE", 0.30),
        "zt": _env_float("LOW_POSITION_W_ZT", 0.25),
    },
}
