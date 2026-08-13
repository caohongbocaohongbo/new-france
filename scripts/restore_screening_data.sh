#!/usr/bin/env bash
set -euo pipefail

DATA_BRANCH="${DATA_BRANCH:-data-snapshots}"

git fetch origin "${DATA_BRANCH}" || {
  echo "未找到 ${DATA_BRANCH}，跳过历史运行数据恢复"
  exit 0
}

restore_file() {
  local file="$1"
  if git cat-file -e "origin/${DATA_BRANCH}:${file}" 2>/dev/null; then
    mkdir -p "$(dirname "${file}")"
    git show "origin/${DATA_BRANCH}:${file}" > "${file}"
    echo "已恢复 ${file}"
  fi
}

restore_file "data/france.md"
restore_file "data/new_france.db"
restore_file "data/source_health.json"
restore_file "data/snapshot_manifest.json"
restore_file "reports/latest.json"
restore_file "reports/task_history.json"
restore_file "reports/overnight_arbitrage_latest.json"
restore_file "reports/overnight_arbitrage_history.json"
restore_file "reports/principal_capital_latest.json"
restore_file "reports/principal_capital_history.json"
restore_file "reports/principal_capital_watchdog_state.json"
restore_file "reports/data_backend/zt_pool.json"
restore_file "reports/data_backend/quotes.json"
restore_file "reports/data_backend/index_snapshot.json"
restore_file "data/principal_capital_source_health.json"
restore_file "data/principal_capital_sina_codes.json"
