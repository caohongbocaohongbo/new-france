#!/bin/bash
# New France — 每日定时运行脚本
# crontab: 10 15 * * 1-5 /bin/bash /Users/fangcang/Documents/claude/projects/new-france/scripts/run_daily.sh
set -e
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"
export PYTHONPATH="$PROJECT_DIR"
LOG_FILE="$PROJECT_DIR/logs/$(date +%Y%m%d).log"
mkdir -p "$PROJECT_DIR/logs"
echo "[$(date '+%H:%M:%S')] New France daily run started" | tee -a "$LOG_FILE"
/usr/bin/python3 -m backend.main 2>&1 | tee -a "$LOG_FILE"
# 第七波智能选股器 + 聚合中枢（路径B：同一进程连跑共享K线缓存；21本地专属失败不阻断）
/usr/bin/python3 -m backend.main --run-smart-picker-all 2>&1 | tee -a "$LOG_FILE"
# 提交 data-snapshots（路径B：本地产数后立即推送，云端 web 兜底读当日数据）
/bin/bash "$PROJECT_DIR/scripts/commit_screening_data.sh" >> "$LOG_FILE" 2>&1 || true
echo "[$(date '+%H:%M:%S')] New France daily run finished" | tee -a "$LOG_FILE"
