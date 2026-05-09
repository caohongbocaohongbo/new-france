#!/bin/bash
# New France — 每日定时运行脚本
# crontab: 10 15 * * 1-5 /bin/bash /path/to/new-france/scripts/run_daily.sh
set -e
cd "$(dirname "$0")/.."
LOG_FILE="logs/$(date +%Y%m%d).log"
mkdir -p logs
echo "[$(date '+%H:%M:%S')] New France daily run started" | tee -a "$LOG_FILE"
python3 -m backend.main 2>&1 | tee -a "$LOG_FILE"
echo "[$(date '+%H:%M:%S')] New France daily run finished" | tee -a "$LOG_FILE"
