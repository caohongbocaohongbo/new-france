"""尾盘隔夜套利插件独立配置。

设计原则：路径与策略参数均支持环境变量覆盖，方便插件迁移到新项目时无需改代码。

迁移时只需设置：
  OA_DATA_DIR   — 数据目录（默认 ${PROJECT_DIR}/data）
  OA_REPORT_DIR — 报告目录（默认 ${PROJECT_DIR}/reports）
  SMTP_*        — 邮件配置（与主项目共用同名环境变量）
"""
import os
from datetime import timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[3]


def _resolve_dir(env_key: str, default: Path) -> Path:
    """优先读环境变量；环境变量未设置或为空时回退到默认路径。"""
    raw = os.environ.get(env_key)
    if not raw:
        return default
    return Path(raw).expanduser().resolve()


DATA_DIR = _resolve_dir("OA_DATA_DIR", PROJECT_DIR / "data")
REPORT_DIR = _resolve_dir("OA_REPORT_DIR", PROJECT_DIR / "reports")
REPORT_FILE = REPORT_DIR / "overnight_arbitrage_latest.json"
HISTORY_FILE = REPORT_DIR / "overnight_arbitrage_history.json"
BEIJING_TZ = timezone(timedelta(hours=8))

CONFIG = {
    "min_amount_yuan": float(os.environ.get("OA_MIN_AMOUNT", "80000000")),
    "score_thresholds": {
        "buy": float(os.environ.get("OA_BUY_SCORE", "55")),
        "watch": float(os.environ.get("OA_WATCH_SCORE", "36")),
        "watch_min_change_pct": float(os.environ.get("OA_WATCH_MIN_CHANGE_PCT", "7")),
        "watch_min_amount_yuan": float(os.environ.get("OA_WATCH_MIN_AMOUNT", "150000000")),
    },
    "smtp_host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
    "smtp_port": int(os.environ.get("SMTP_PORT", "587")),
    "smtp_user": os.environ.get("SMTP_USER", ""),
    "smtp_password": os.environ.get("SMTP_PASSWORD", ""),
    "smtp_to": os.environ.get("SMTP_TO", ""),
}
