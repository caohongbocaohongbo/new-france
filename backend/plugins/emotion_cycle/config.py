"""情绪周期插件配置。"""
import os
from datetime import timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[3]
REPORT_DIR = PROJECT_DIR / "reports"
DATA_DIR = PROJECT_DIR / "data"
BEIJING_TZ = timezone(timedelta(hours=8))
SNAPSHOT_NAME = "emotion"

CONFIG = {
    "weights": {
        "zt_count": float(os.environ.get("EMOTION_W_ZT_COUNT", 20)),
        "max_height": float(os.environ.get("EMOTION_W_MAX_HEIGHT", 20)),
        "promotion": float(os.environ.get("EMOTION_W_PROMOTION", 20)),
        "survival": float(os.environ.get("EMOTION_W_SURVIVAL", 20)),
        "premium": float(os.environ.get("EMOTION_W_PREMIUM", 10)),
        "index": float(os.environ.get("EMOTION_W_INDEX", 10)),
    },
}
