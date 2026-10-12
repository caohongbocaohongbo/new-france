"""26 条件盯盘插件配置。"""
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[3]
REPORT_DIR = PROJECT_DIR / "reports"
SNAPSHOT_NAME = "conditional_watch"
DATA_DIR = PROJECT_DIR / "data"

POLL_INTERVAL_SECONDS = int(os.environ.get("WATCH_POLL_INTERVAL", 5))
DEFAULT_CHANNELS = ["openclaw"]
