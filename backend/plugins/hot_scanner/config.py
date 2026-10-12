"""26 舆情热度聚合插件配置。"""
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[3]
REPORT_DIR = PROJECT_DIR / "reports"
SNAPSHOT_NAME = "hot_scanner"

# 各源计分权重
SCORE_RULE = {
    "eastmoney_rank": {"top10": 3, "top50": 2, "top100": 1},
    "xueqiu_hot": {"per_hit": 1},
    "guba_hot": {"hot": 2, "mention": 1},
    "ths_rank": {"top100": 1},
}
TIMEOUT_SECONDS = float(os.environ.get("HOT_SCAN_TIMEOUT", 8))
