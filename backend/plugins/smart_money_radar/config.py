"""smart_money_radar 配置。"""
import os
from datetime import timedelta, timezone
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_DIR / "data"
REPORT_DIR = PROJECT_DIR / "reports"
LOG_DIR = PROJECT_DIR / "logs"
BEIJING_TZ = timezone(timedelta(hours=8))

LATEST_FILE = REPORT_DIR / "smart_money_radar_latest.json"
HISTORY_FILE = REPORT_DIR / "smart_money_radar_history.json"
STATE_DIR = DATA_DIR
NOTIFIED_DIR = DATA_DIR
REPLAY_DB_FILE = DATA_DIR / "smart_money_radar.sqlite3"
RADAR_SOURCE = os.environ.get("RADAR_SOURCE", "local").strip() or "local"


def _env(name: str, default):
    return os.environ.get(f"RADAR_{name}", default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(_env(name, default)))
    except (TypeError, ValueError):
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, default))
    except (TypeError, ValueError):
        return float(default)


def _env_bool(name: str, default: bool) -> bool:
    value = str(_env(name, str(default))).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _env_csv(name: str, default: str) -> list:
    return [item.strip() for item in str(_env(name, default)).split(",") if item.strip()]


CONFIG = {
    "tdx_servers": _env_csv(
        "TDX_SERVERS",
        "115.238.90.165:7709,119.147.212.81:7709,47.103.48.45:7709",
    ),
    "tdx_connect_timeout_s": _env_float("TDX_CONNECT_TIMEOUT_S", 3),
    "tdx_socket_timeout_s": _env_float("TDX_SOCKET_TIMEOUT_S", 3),
    "failure_threshold": _env_int("FAILURE_THRESHOLD", 5),
    "circuit_break_minutes": _env_int("CIRCUIT_BREAK_MINUTES", 8),
    "tx_count": _env_int("TX_COUNT", 800),
    "fetch_concurrency": _env_int("FETCH_CONCURRENCY", 1),
    "pool_source_file": _env("POOL_SOURCE_FILE", str(REPORT_DIR / "principal_capital_latest.json")),
    # 主契约是候选列表；既有 principal_capital 报告的 buy_candidates/sell_candidates
    # 当前为数量，service 会兼容回退到 buy_triggered/sell_triggered 列表。
    "pool_keys": _env_csv("POOL_KEYS", "buy_candidates,sell_candidates"),
    "pool_max": _env_int("POOL_MAX", 40),
    "pool_refresh_min": _env_int("POOL_REFRESH_MIN", 10),
    "exclude_gem": _env_bool("EXCLUDE_GEM", True),
    "exclude_star": _env_bool("EXCLUDE_STAR", True),
    "poll_interval_s": _env_int("POLL_INTERVAL_S", 4),
    "decay_window_s": _env_int("DECAY_WINDOW_S", 90),
    "decay_minutes": _env_int("DECAY_MINUTES", 3),
    "strength_smooth_frames": _env_int("STRENGTH_SMOOTH_FRAMES", 3),
    "bar_cache_ttl_s": _env_int("BAR_CACHE_TTL_S", 45),
    "strength_threshold": _env_float("STRENGTH_THRESHOLD", 65),
    "active_buy_threshold": _env_float("ACTIVE_BUY_THRESHOLD", 0.55),
    "main_inflow_ratio_threshold": _env_float("MAIN_INFLOW_RATIO_THRESHOLD", 55),
    "main_net_inflow_threshold": _env_float("MAIN_NET_INFLOW_THRESHOLD", 0),
    "latent_rounds": _env_int("LATENT_ROUNDS", 3),
    "latent_change_max": _env_float("LATENT_CHANGE_MAX", 0.015),
    "latent_volume_ratio": _env_float("LATENT_VOLUME_RATIO", 1.2),
    "absorption_active_buy": _env_float("ABSORPTION_ACTIVE_BUY", 0.60),
    "absorption_minutes": _env_float("ABSORPTION_MINUTES", 5),
    "absorption_change_max": _env_float("ABSORPTION_CHANGE_MAX", 0.02),
    "prelaunch_smart_score": _env_float("PRELAUNCH_SMART_SCORE", 80),
    "prelaunch_launch_score": _env_float("PRELAUNCH_LAUNCH_SCORE", 80),
    "prelaunch_distance_high": _env_float("PRELAUNCH_DISTANCE_HIGH", 0.01),
    "prelaunch_price_impact_max": _env_float("PRELAUNCH_PRICE_IMPACT_MAX", 1.0),
    "buy_trend_threshold": _env_float("BUY_TREND_THRESHOLD", 0.2),
    "absorption_net_inflow_threshold": _env_float("ABSORPTION_NET_INFLOW_THRESHOLD", 0),
    "decay_threshold": _env_float("DECAY_THRESHOLD", 0.3),
    "failure_sell_surge_ratio": _env_float("FAILURE_SELL_SURGE_RATIO", 2.0),
    "launch_volume_ratio": _env_float("LAUNCH_VOLUME_RATIO", 1.5),
    "smart_money_weights": {
        "active_buy_ratio": _env_float("WEIGHT_ACTIVE_BUY_RATIO", 20),
        "fund_persistence": _env_float("WEIGHT_FUND_PERSISTENCE", 15),
        "price_impact": _env_float("WEIGHT_PRICE_IMPACT", 20),
        "volume_ratio": _env_float("WEIGHT_VOLUME_RATIO", 10),
        "decay_score": _env_float("WEIGHT_DECAY_SCORE", 10),
        "strength_smooth": _env_float("WEIGHT_STRENGTH", 10),
        "vwap_deviation": _env_float("WEIGHT_VWAP", 5),
        "return_1m": _env_float("WEIGHT_SHORT_STRUCTURE", 5),
        "sector_fund": _env_float("WEIGHT_SECTOR_FUND", 5),
    },
    "launch_weights": {
        "decay": _env_float("WEIGHT_LAUNCH_DECAY", 25),
        "buy_trend": _env_float("WEIGHT_BUY_TREND", 20),
        "near_high": _env_float("WEIGHT_NEAR_HIGH", 20),
        "volume": _env_float("WEIGHT_LAUNCH_VOLUME", 20),
        "vwap": _env_float("WEIGHT_LAUNCH_VWAP", 15),
    },
    "alert_cooldown_minutes": _env_int("ALERT_COOLDOWN_MINUTES", 30),
    "history_keep_days": _env_int("HISTORY_KEEP_DAYS", 7),
    "radar_source": RADAR_SOURCE,
    "enable_sqlite_dump": _env_bool("ENABLE_SQLITE_DUMP", False),
}
