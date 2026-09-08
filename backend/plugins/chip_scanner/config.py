"""筹码集中度与获利盘选股配置（本地专属：pytdx 分钟 K 近似分价）。"""
import os
from datetime import timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[3]
REPORT_DIR = PROJECT_DIR / "reports"
DATA_DIR = PROJECT_DIR / "data"
BEIJING_TZ = timezone(timedelta(hours=8))
SNAPSHOT_NAME = "chip_scanner"


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _env_bool(name, default):
    return str(os.environ.get(name, str(default))).strip().lower() in {"1", "true", "yes", "on"}


CONFIG = {
    # 命中阈值
    "concentration_max": _env_float("CHIP_CONCENTRATION_MAX", 0.15),
    "profit_min": _env_float("CHIP_PROFIT_MIN", 0.70),
    "tight_concentration_max": _env_float("CHIP_TIGHT_CONCENTRATION_MAX", 0.10),
    "tight_profit_min": _env_float("CHIP_TIGHT_PROFIT_MIN", 0.85),
    # 粗筛
    "min_amount": _env_float("CHIP_MIN_AMOUNT", 5e7),
    # K 线
    "kline_days": int(_env_float("CHIP_KLINE_DAYS", 60)),
    "kline_workers": int(_env_float("CHIP_KLINE_WORKERS", 30)),
    # 展示层过滤（默认仅主板，CHIP_* env 可开创业板/科创板）
    "show_gem": _env_bool("CHIP_SHOW_GEM", False),
    "show_star": _env_bool("CHIP_SHOW_STAR", False),
    # 本地专属标记（东财分价接口不可用，降级为 pytdx 分钟 K 近似）
    "local_only": _env_bool("CHIP_LOCAL_ONLY", True),
    # 高度控盘🔥邮件推送冷却（分钟，仿 15 RED 信号推送）
    "notify_cooldown_minutes": int(_env_float("CHIP_NOTIFY_COOLDOWN_MINUTES", 30)),
}
