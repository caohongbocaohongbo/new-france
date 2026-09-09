"""22 智能选股聚合中枢配置。"""
import os
from datetime import timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[3]
REPORT_DIR = PROJECT_DIR / "reports"
DATA_DIR = PROJECT_DIR / "data"
BEIJING_TZ = timezone(timedelta(hours=8))
SNAPSHOT_NAME = "smart_picker"
# 图表预计算体量大，单独成快照（主快照保持 ≤120KB 体积预算）
CHARTS_SNAPSHOT_NAME = "smart_picker_charts"

STRATEGY_KEYS = ("tech", "trend", "pattern", "chip")
SNAPSHOT_MAP = {
    "tech": "tech_indicators",
    "trend": "trend_strength",
    "pattern": "pattern_scanner",
    "chip": "chip_scanner",
}
SCORE_KEYS = {
    "tech": "tech_score", "trend": "trend_score",
    "pattern": "pattern_score", "chip": "chip_score",
}
PCT_KEYS = {
    "tech": "tech_score_pct", "trend": "trend_score_pct",
    "pattern": "pattern_score_pct", "chip": "chip_score_pct",
}
POOL_KEYS = ("resonance", "tech", "trend", "pattern", "chip")
SORT_WHITELIST = (
    "hub_score", "hit_strategies", "price", "change_pct", "total_amount",
    "tech_score", "trend_score", "pattern_score", "chip_score", "code",
)
BADGE_SOURCES = {
    "fund_flow": "principal_capital",
    "tier_state": "tier_flow",
    "radar": "smart_money_radar",
}


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _env_int(name, default):
    return int(_env_float(name, default))


def _env_bool(name, default):
    return str(os.environ.get(name, str(default))).strip().lower() in {"1", "true", "yes", "on"}


def _env_weights():
    defaults = {"tech": 0.30, "trend": 0.25, "pattern": 0.20, "chip": 0.25}
    out = {}
    for k, d in defaults.items():
        v = _env_float(f"HUB_W_{k.upper()}", d)
        out[k] = v if v > 0 else d  # 非法值回退默认
    return out


CONFIG = {
    # 统一分权重（初值经验定，后续 06 因子实验室 IC 校准；自动归一化）
    "weights": _env_weights(),
    # 共振阈值：命中策略数 ≥ resonance_min_hit
    "resonance_min_hit": _env_int("HUB_RESONANCE_MIN_HIT", 2),
    # 门控：当日涨停剔除（zt_pool 不可用时跳过并显式标注）
    "exclude_limit_up": _env_bool("HUB_EXCLUDE_LIMIT_UP", True),
    "show_gem": _env_bool("HUB_SHOW_GEM", False),
    "show_star": _env_bool("HUB_SHOW_STAR", False),
    "min_amount": _env_float("HUB_MIN_AMOUNT", 0.0),
    # 榜单截断
    "top_n": _env_int("HUB_TOP_N", 100),
    "pool_cap": _env_int("HUB_POOL_CAP", 40),
    # 图表预计算
    "chart_precompute_n": _env_int("HUB_CHART_PRECOMPUTE_N", 40),
    "chart_days": _env_int("HUB_CHART_DAYS", 80),
    # 信号质量追踪
    "perf_windows": [int(x) for x in str(os.environ.get("HUB_PERF_WINDOWS", "1,3,5")).split(",") if x.strip().isdigit()],
    "perf_lookback_days": _env_int("HUB_PERF_LOOKBACK_DAYS", 20),
    "perf_sample_min": _env_int("HUB_PERF_SAMPLE_MIN", 20),
    "perf_track_top_n": _env_int("HUB_PERF_TRACK_TOP_N", 60),
    # 共振池🔥邮件推送冷却（分钟）
    "notify_cooldown_minutes": _env_int("HUB_NOTIFY_COOLDOWN_MINUTES", 30),
    # web 远端 raw 兜底 TTL（秒，可选 common 增强，本期保留配置位）
    "remote_ttl_seconds": _env_int("HUB_REMOTE_TTL_SECONDS", 600),
}
