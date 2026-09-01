"""龙虎榜插件配置。"""
import os
from datetime import timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[3]
REPORT_DIR = PROJECT_DIR / "reports"
DATA_DIR = PROJECT_DIR / "data"
BEIJING_TZ = timezone(timedelta(hours=8))
SNAPSHOT_NAME = "lhb"
CONFIG = {
    "fetch_timeout_s": float(os.environ.get("LHB_TIMEOUT_S", "15")),
    "max_seat_detail_stocks": int(os.environ.get("LHB_MAX_SEAT_DETAIL", "20")),
}
