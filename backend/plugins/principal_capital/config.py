"""主力资金插件独立配置（不污染主项目 config/strategy_params.py）。

迁移到新项目时，此文件可直接复用，或通过环境变量覆盖任意配置项。
"""
import os
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    return str(os.environ.get(name, str(default))).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


def _env_csv(name: str, default: str) -> list:
    return [item.strip() for item in str(os.environ.get(name, default)).split(",") if item.strip()]


# 插件数据/报告目录（与主项目共享 data/ reports/，但文件名都加 plugin 前缀避免冲突）
PROJECT_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_DIR / "data"
REPORT_DIR = PROJECT_DIR / "reports"

# 阈值与策略参数
CONFIG = {
    # 信号阈值
    "buy_threshold_ratio": float(os.environ.get("PC_BUY_THRESHOLD", "50.0")),
    "sell_threshold_ratio": float(os.environ.get("PC_SELL_THRESHOLD", "30.0")),
    "sell_severity_thresholds": {"warn": 30.0, "alert": 40.0, "danger": 50.0},

    # 票池
    "exclude_star": os.environ.get("PC_EXCLUDE_STAR", "true").lower() == "true",
    "min_amount_yuan": float(os.environ.get("PC_MIN_AMOUNT", "10000000")),

    # 去重
    "buy_dedup_minutes": 10000,                # 当日不重复
    "sell_dedup_cooldown_minutes": 60,         # 卖出 60min 冷却
    "history_keep_days": 7,

    # 调度
    "scan_interval_minutes": 5,

    # 数据源熔断
    "data_source_cache_ttl_seconds": 1800,
    "data_source_circuit_break_minutes": 8,
    "data_source_failure_threshold": 5,
    "enable_verify_sampling": False,

    # 新浪兜底源
    "sina_max_workers": 40,                       # 并发线程数（美国 IP 实测 40 并发仍 100% 成功）
    "sina_codes_cache_ttl_seconds": 259200,       # 主板代码清单缓存 3 天（新股极少，省每轮翻页 ~25s）

    # ---- 23 v2：执行模式与唯一写者 ----
    # 副作用总开关：official=GitHub Actions 唯一正式写者；shadow=只写 *_shadow.json 与独立状态；
    # readonly=仅诊断，不发信、不写 official 状态。未设置时默认 readonly，避免本机误发邮件。
    "execution_mode": os.environ.get("PC_EXECUTION_MODE", "readonly").strip().lower() or "readonly",
    "official_owner": os.environ.get("PC_OFFICIAL_OWNER", "").strip(),
    "owner_lease_seconds": _env_int("PC_OWNER_LEASE_SECONDS", 600),

    # strict/hybrid 流水线。初始不得默认 hybrid。
    "pipeline_mode": os.environ.get("PC_PIPELINE_MODE", "strict").strip().lower() or "strict",
    "bulk_admitted": _env_bool("PC_BULK_ADMITTED", False),

    # bulk 粗筛经验参数（不是安全边界；正式准入阈值由 M5 的 5 日 shadow 数据分布产生）
    "coarse_buy_ratio": _env_float("PC_COARSE_BUY_RATIO", 40.0),
    "coarse_sell_ratio": _env_float("PC_COARSE_SELL_RATIO", -24.0),
    # r0_net / r0_ratio 极值头部数量（每侧），初值仅供 shadow 观察，不承诺不漏
    "coarse_r0_head": _env_int("PC_COARSE_R0_HEAD", 50),

    # 精算阶段
    "refine_workers": _env_int("PC_REFINE_WORKERS", 20),
    "refine_qps": _env_int("PC_REFINE_QPS", 20),
    "round_deadline_seconds": _env_int("PC_ROUND_DEADLINE_SECONDS", 90),
    "refine_timeout_seconds": _env_float("PC_REFINE_TIMEOUT_SECONDS", 8.0),

    # hybrid 持续审计
    "complement_audit_size": _env_int("PC_COMPLEMENT_AUDIT_SIZE", 300),
    "sentinel_times": _env_csv("PC_SENTINEL_TIMES", "09:35,10:30,13:30,14:30,14:50"),

    # 质量门控：默认禁止未知源时间的 direct 通知
    "allow_provisional_notify": _env_bool("PC_ALLOW_PROVISIONAL_NOTIFY", False),

    # 同源日内特征
    "intraday_max_points": _env_int("PC_INTRADAY_MAX_POINTS", 24),

    # 摘要 finalizer
    "summary_max_age_min": _env_int("PC_SUMMARY_MAX_AGE_MIN", 15),

    # 邮件 SMTP（与主项目共享相同环境变量名以复用 secrets）
    "smtp_host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
    "smtp_port": int(os.environ.get("SMTP_PORT", "587")),
    "smtp_user": os.environ.get("SMTP_USER", ""),
    "smtp_password": os.environ.get("SMTP_PASSWORD", ""),
    "smtp_to": os.environ.get("SMTP_TO", ""),
}


# 文件路径（插件独立命名空间）
REPORT_FILE = REPORT_DIR / "principal_capital_latest.json"
SHADOW_REPORT_FILE = REPORT_DIR / "principal_capital_shadow.json"
HISTORY_FILE = REPORT_DIR / "principal_capital_history.json"
SOURCE_HEALTH_FILE = DATA_DIR / "principal_capital_source_health.json"
CACHE_FILE = DATA_DIR / "principal_capital_cache.parquet"
SINA_CODES_CACHE_FILE = DATA_DIR / "principal_capital_sina_codes.json"
INTRADAY_STATE_FILE = DATA_DIR / "principal_capital_intraday_state.json"
SHADOW_STATE_FILE = DATA_DIR / "principal_capital_intraday_state_shadow.json"
MANUAL_STATUS_FILE = DATA_DIR / "principal_capital_manual_status.json"
OWNER_CONFLICT_FILE = DATA_DIR / "principal_capital_owner_conflict.json"
M5_AUDIT_FILE = DATA_DIR / "principal_capital_m5_audit.json"
M5_AUDIT_MAX_RECORDS = 1000

# data-snapshots 分支的 GitHub raw 前缀。
# 用途：Render Web Service 自身取不到东财数据（IP 被封），读接口在本地报告为空时
# 回退到此处拉取 GitHub Actions 已生成的快照 JSON，保证页面与邮件同源。
# 允许用环境变量 PC_SNAPSHOT_RAW_BASE 覆盖（迁移到其它仓库时用）。
SNAPSHOT_RAW_BASE = os.environ.get(
    "PC_SNAPSHOT_RAW_BASE",
    "https://raw.githubusercontent.com/caohongbocaohongbo/new-france/data-snapshots",
).rstrip("/")


def notified_file(today_iso: str, direction: str) -> Path:
    """当日去重文件路径。"""
    return DATA_DIR / f"principal_capital_{direction}_notified_{today_iso}.json"


def resolve_execution_mode(explicit: str = None) -> str:
    """解析执行模式。非法值抛 ValueError；默认 readonly（避免本机误发邮件）。"""
    mode = (explicit or CONFIG["execution_mode"]).strip().lower()
    if mode not in {"official", "shadow", "readonly"}:
        raise ValueError(f"非法 PC_EXECUTION_MODE: {mode!r}")
    return mode


def resolve_pipeline_mode(explicit: str = None) -> str:
    """解析流水线模式。M6 完成前 hybrid 未实现，代码层拒绝，effective 只能是 strict。"""
    requested = (explicit or CONFIG["pipeline_mode"]).strip().lower()
    if requested not in {"strict", "hybrid"}:
        raise ValueError(f"非法 PC_PIPELINE_MODE: {requested!r}")
    if requested == "hybrid":
        # P0-3：不得出现“报告为 hybrid、实际跑 strict”的假执行状态
        raise RuntimeError(
            "hybrid 尚未完成生产实现（M6 未完成），禁止启用；本阶段 effective 只能为 strict"
        )
    return "strict"

def atomic_write_json(path, payload) -> None:
    """统一 JSON 原子写（临时文件 + os.replace），供 official/shadow/manual 报告共用。"""
    import json as _json
    import math as _math
    import os as _os
    import uuid as _uuid

    def _safe(value):
        if isinstance(value, float):
            return value if _math.isfinite(value) else None
        if isinstance(value, dict):
            return {k: _safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_safe(v) for v in value]
        return value

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + f".tmp.{_os.getpid()}.{_uuid.uuid4().hex[:8]}")
    tmp.write_text(
        _json.dumps(_safe(payload), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    _os.replace(tmp, target)


def config_fingerprint() -> str:
    """M5 审计用配置指纹：粗筛/阈值/票池等影响候选集合的参数变更会改变指纹。"""
    import hashlib as _hashlib

    keys = (
        "buy_threshold_ratio", "sell_threshold_ratio", "exclude_star", "min_amount_yuan",
        "coarse_buy_ratio", "coarse_sell_ratio", "coarse_r0_head",
    )
    text = "|".join(f"{key}={CONFIG.get(key)}" for key in keys)
    return _hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
