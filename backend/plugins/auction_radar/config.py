"""28 低位竞价雷达配置（RADAR_AUCTION_* 环境变量）。"""
import os
from datetime import timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_DIR / "data"
REPORT_DIR = PROJECT_DIR / "reports"
BEIJING_TZ = timezone(timedelta(hours=8))

SNAPSHOT_NAME = "auction_radar"
LATEST_FILE = REPORT_DIR / "auction_radar_latest.json"
PRELIMINARY_FILE = REPORT_DIR / "auction_radar_preliminary.json"
OUTCOME_DIR = DATA_DIR / "auction_outcomes"


def _env(name, default):
    return os.environ.get(f"RADAR_AUCTION_{name}", default)


def _env_int(name, default):
    try:
        return int(float(_env(name, default)))
    except (TypeError, ValueError):
        return int(default)


def _env_float(name, default):
    try:
        return float(_env(name, default))
    except (TypeError, ValueError):
        return float(default)


def _env_bool(name, default):
    return str(_env(name, str(default))).strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name, default):
    return [x.strip() for x in str(_env(name, default)).split(",") if x.strip()]


CONFIG = {
    # 池预热与冻结
    "prewarm_at": _env("PREWARM_AT", "09:10"),
    "pool_freeze_at": _env("POOL_FREEZE_AT", "09:14:30"),
    "read_only_query": _env_bool("READ_ONLY_QUERY", True),
    "pool_max": _env_int("POOL_MAX", 400),
    "allow_stale_notify": _env_bool("ALLOW_STALE_NOTIFY", False),
    "producer_deadline": _env("PRODUCER_DEADLINE", "15:50"),
    "low_snapshot_name": _env("LOW_SNAPSHOT_NAME", "low_position"),
    # 采集
    "base_poll_s": _env_float("BASE_POLL_S", 5),
    "single_flight": _env_bool("SINGLE_FLIGHT", True),
    "batch_size": _env_int("BATCH_SIZE", 60),
    "concurrency": _env_int("CONCURRENCY", 4),
    "hot_poll_enabled": _env_bool("HOT_POLL_ENABLED", False),
    "legacy_auction_enabled": _env_bool("LEGACY_AUCTION_ENABLED", False),
    "http_timeout_s": _env_float("HTTP_TIMEOUT_S", 5),
    # 最终确认
    "final_confirm_enabled": _env_bool("FINAL_CONFIRM_ENABLED", True),
    "final_fast_deadline_s": _env_float("FINAL_FAST_DEADLINE_S", 15),
    "final_confirm_deadline_s": _env_float("FINAL_CONFIRM_DEADLINE_S", 60),
    "final_attempt_budget_s": _env_float("FINAL_ATTEMPT_BUDGET_S", 5),
    "final_retry_interval_s": _env_float("FINAL_RETRY_INTERVAL_S", 1),
    "require_final_confirmed": _env_bool("REQUIRE_FINAL_CONFIRMED", True),
    "final_scope": _env("FINAL_SCOPE", "full_frozen_pool"),
    "top_n": _env_int("TOP_N", 10),
    "partial_min_confirmed": _env_int("PARTIAL_MIN_CONFIRMED", 3),
    "required_final_fields": _env_csv("REQUIRED_FINAL_FIELDS", "open_price"),
    # 质量与策略
    "quote_quality_check": _env_bool("QUOTE_QUALITY_CHECK", True),
    "min_factor_coverage": _env_float("MIN_FACTOR_COVERAGE", 0.70),
    "strategy_version": _env("STRATEGY_VERSION", "probe_only"),
    "branch": _env("BRANCH", "UNVERIFIED"),
    "field_capability_version": _env("FIELD_CAPABILITY_VERSION", "unverified"),
    "factor_spec_version": _env("FACTOR_SPEC_VERSION", "auction_factor_v1"),
    "preview_min_days": _env_int("PREVIEW_MIN_DAYS", 10),
    "preview_min_samples": _env_int("PREVIEW_MIN_SAMPLES", 100),
    "preview_notify_enabled": _env_bool("PREVIEW_NOTIFY_ENABLED", True),
    # 推送与运维
    "outbox_enabled": _env_bool("OUTBOX_ENABLED", False),
    "outbox_retry_max": _env_int("OUTBOX_RETRY_MAX", 2),
    "outbox_channel_concurrency": _env_int("OUTBOX_CHANNEL_CONCURRENCY", 4),
    "outbox_sending_timeout_s": _env_int("OUTBOX_SENDING_TIMEOUT_S", 30),
    "latency_metrics": _env_bool("LATENCY_METRICS", True),
    "smtp_connect_healthcheck": _env_bool("SMTP_CONNECT_HEALTHCHECK", True),
    "trigger_enabled": _env_bool("TRIGGER_ENABLED", False),
    "trigger_token": os.environ.get("RADAR_AUCTION_TRIGGER_TOKEN", ""),
    # 全市场竞价快照旁路采样
    "market_sample_enabled": _env_bool("MARKET_SAMPLE_ENABLED", True),
    "market_sample_times": _env_csv("MARKET_SAMPLE_TIMES", "09:20,09:24:50"),
    "market_sample_dir": os.environ.get(
        "RADAR_AUCTION_MARKET_SAMPLE_DIR", str(DATA_DIR / "auction_market_samples")
    ),
    # 数据范围（仅沪深主板 A 股）
    "show_gem": False,
    "show_star": False,
    # 合成权重（示例，预览期冻结）
    "w_low": _env_float("W_LOW", 0.40),
    "w_auction": _env_float("W_AUCTION", 0.60),
}
