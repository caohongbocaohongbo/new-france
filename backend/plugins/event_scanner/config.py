"""26 事件驱动雷达插件配置。"""
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[3]
REPORT_DIR = PROJECT_DIR / "reports"
SNAPSHOT_NAME = "event_scanner"
DATA_DIR = PROJECT_DIR / "data"
TIMEOUT_SECONDS = float(os.environ.get("EVENT_SCAN_TIMEOUT", 10))
