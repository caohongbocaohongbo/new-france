"""主力资金插件独立配置（不污染主项目 config/strategy_params.py）。

迁移到新项目时，此文件可直接复用，或通过环境变量覆盖任意配置项。
"""
import os
from pathlib import Path

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

    # 邮件 SMTP（与主项目共享相同环境变量名以复用 secrets）
    "smtp_host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
    "smtp_port": int(os.environ.get("SMTP_PORT", "587")),
    "smtp_user": os.environ.get("SMTP_USER", ""),
    "smtp_password": os.environ.get("SMTP_PASSWORD", ""),
    "smtp_to": os.environ.get("SMTP_TO", ""),
}


# 文件路径（插件独立命名空间）
REPORT_FILE = REPORT_DIR / "principal_capital_latest.json"
HISTORY_FILE = REPORT_DIR / "principal_capital_history.json"
SOURCE_HEALTH_FILE = DATA_DIR / "principal_capital_source_health.json"
CACHE_FILE = DATA_DIR / "principal_capital_cache.parquet"

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
