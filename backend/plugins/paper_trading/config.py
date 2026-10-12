"""26 绩效模拟盘插件配置。"""
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[3]
REPORT_DIR = PROJECT_DIR / "reports"
SNAPSHOT_NAME = "paper_trading"

# 仓位与费用（主板口径，A股真实成本）
INITIAL_CAPITAL = float(os.environ.get("PAPER_INITIAL_CAPITAL", 100000.0))
LEVEL_WEIGHT = {"STRONG_BUY": 0.03, "BUY": 0.02}
MAX_SINGLE_POSITION = 0.05       # 单票上限 5%
MAX_TOTAL_POSITION = 0.60        # 总仓位上限 60%
BUY_FEE_RATE = 0.00025           # 买入佣金万2.5
SELL_FEE_RATE = 0.00025 + 0.001  # 卖出佣金 + 印花税千1

# 主板代码前缀（排除创业板/科创板）
MAIN_BOARD_PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003")


def is_main_board(code) -> bool:
    return str(code or "").zfill(6).startswith(MAIN_BOARD_PREFIXES)
