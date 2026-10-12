#!/bin/bash
# New France — 每日定时运行脚本
# crontab: 10 15 * * 1-5 /bin/bash /Users/fangcang/Documents/claude/projects/new-france/scripts/run_daily.sh
set -eo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"
export PYTHONPATH="$PROJECT_DIR"
LOG_FILE="$PROJECT_DIR/logs/$(date +%Y%m%d).log"
mkdir -p "$PROJECT_DIR/logs"
echo "[$(date '+%H:%M:%S')] New France daily run started" | tee -a "$LOG_FILE"
/usr/bin/python3 -m backend.main 2>&1 | tee -a "$LOG_FILE"

# 插件28 盘后产池接线（M0）：主筛选完成后、其它可延后任务之前
PRODUCER_START=$(date +%s)
/usr/bin/python3 -m backend.main --run-low-position-once 2>&1 | tee -a "$LOG_FILE"
PRODUCER_END=$(date +%s)
PRODUCER_HHMM=$(date '+%H:%M')
# 业务状态核查：low_position 快照必须 completed 且日期为今日；失败即非零退出
python3 -c "import json;from pathlib import Path;from datetime import date;snap=json.loads(Path('reports/low_position_latest.json').read_text(encoding='utf-8')) if Path('reports/low_position_latest.json').exists() else {};today=date.today().isoformat();ok=snap.get('status')=='completed' and str(snap.get('date'))==today;print('[产池核验]', 'OK' if ok else 'FAIL', 'status=',snap.get('status'),'date=',snap.get('date'),'expect=',today,'count=',snap.get('total_count'));raise SystemExit(0 if ok else 1)" 2>&1 | tee -a "$LOG_FILE"
if [ "$PRODUCER_HHMM" \> "15:50" ]; then
  echo "[产池截止] 完成于 $PRODUCER_HHMM，超过 15:50 deadline" | tee -a "$LOG_FILE"
  exit 1
fi

# 第七波智能选股器 + 聚合中枢（可延后任务；21本地专属失败不阻断）
/usr/bin/python3 -m backend.main --run-smart-picker-all 2>&1 | tee -a "$LOG_FILE"
# 技术指标等插件任务（可扩展清单 + 线程池并发 + 超时；阻断任务失败才非零退出）
/usr/bin/python3 "$PROJECT_DIR/scripts/run_plugin_tasks.py" 2>&1 | tee -a "$LOG_FILE"
# 提交 data-snapshots（路径B：本地产数后立即推送，云端 web 兜底读当日数据）
/bin/bash "$PROJECT_DIR/scripts/commit_screening_data.sh" >> "$LOG_FILE" 2>&1 || true
echo "[$(date '+%H:%M:%S')] New France daily run finished" | tee -a "$LOG_FILE"
