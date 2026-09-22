#!/usr/bin/env bash
set -euo pipefail

BASE_BRANCH="${BASE_BRANCH:-main}"
DATA_BRANCH="${DATA_BRANCH:-data-snapshots}"

if [[ "${SKIP_DATA_SNAPSHOT_COMMIT:-0}" == "1" ]]; then
  echo "SKIP_DATA_SNAPSHOT_COMMIT=1，跳过生成数据快照提交"
  exit 0
fi

git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"

git fetch origin "${BASE_BRANCH}" --prune
git fetch origin "${DATA_BRANCH}" || true
HEAD_SHA="$(git rev-parse HEAD)"
REMOTE_BASE_SHA="$(git rev-parse "origin/${BASE_BRANCH}")"

if [[ "${HEAD_SHA}" != "${REMOTE_BASE_SHA}" ]]; then
  echo "当前 workflow 运行在旧提交 ${HEAD_SHA}，origin/${BASE_BRANCH} 已是 ${REMOTE_BASE_SHA}，跳过数据分支提交"
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
snapshot_dir="$(mktemp -d)"
data_worktree_dir="$(mktemp -d)"
picker_keep_dir="$(mktemp -d)"
# 22 方案：picker 快照白名单（与 restore_screening_data.sh 保持一致）
PICKER_FILES=(
  reports/tech_indicators_latest.json
  reports/trend_strength_latest.json
  reports/pattern_scanner_latest.json
  reports/chip_scanner_latest.json
  reports/smart_picker_latest.json
  reports/smart_picker_charts_latest.json
  reports/data_backend/tech_indicators_latest.json
  reports/data_backend/trend_strength_latest.json
  reports/data_backend/pattern_scanner_latest.json
  reports/data_backend/chip_scanner_latest.json
  reports/data_backend/smart_picker_latest.json
  reports/data_backend/smart_picker_charts_latest.json
)
cleanup() {
  rm -rf "${snapshot_dir}"
  git worktree remove --force "${data_worktree_dir}" >/dev/null 2>&1 || true
  rm -rf "${data_worktree_dir}"
  rm -rf "${picker_keep_dir}"
}
trap cleanup EXIT

mkdir -p "${snapshot_dir}/data" "${snapshot_dir}/reports"

for file in data/france.md data/new_france.db data/source_health.json data/principal_capital_source_health.json data/principal_capital_sina_codes.json data/principal_capital_intraday_state.json data/principal_capital_intraday_state_shadow.json data/principal_capital_owner_conflict.json data/principal_capital_m5_audit.json; do
  if [[ -f "${file}" ]]; then
    cp "${file}" "${snapshot_dir}/${file}"
  fi
done

# 23 v2：通知去重文件按日期落盘，一并进入快照（避免双写/重复通知）
for nfile in data/principal_capital_*_notified_*.json; do
  if [[ -f "${nfile}" ]]; then
    cp "${nfile}" "${snapshot_dir}/${nfile}"
  fi
done

if [[ -d reports ]]; then
  cp -R reports/. "${snapshot_dir}/reports/"
  rm -rf "${snapshot_dir}/reports/.cache"  # 22 方案 §2.4 磁盘 TTL 缓存不进 git
fi

cat > "${snapshot_dir}/data/snapshot_manifest.json" <<EOF
{
  "source_branch": "${BASE_BRANCH}",
  "source_sha": "${HEAD_SHA}",
  "generated_at_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "data_branch": "${DATA_BRANCH}"
}
EOF
# 核验补充：逐文件 size + SHA-256 清单（含 reports/ 全部生成物；脚本缺失时跳过，兼容合成沙箱）
if [[ -f "${SCRIPT_DIR}/snapshot_manifest.py" ]]; then
  python3 "${SCRIPT_DIR}/snapshot_manifest.py" gen "${snapshot_dir}" "${snapshot_dir}/data/snapshot_manifest.json"
fi

if git show-ref --verify --quiet "refs/remotes/origin/${DATA_BRANCH}"; then
  git worktree add --detach --force "${data_worktree_dir}" "origin/${DATA_BRANCH}"
else
  git worktree add --detach --force "${data_worktree_dir}" "${HEAD_SHA}"
fi

git -C "${data_worktree_dir}" config user.name "github-actions[bot]"
git -C "${data_worktree_dir}" config user.email "github-actions[bot]@users.noreply.github.com"

(
  cd "${data_worktree_dir}"

  if git show-ref --verify --quiet "refs/remotes/origin/${DATA_BRANCH}"; then
    git checkout -B "${DATA_BRANCH}" "origin/${DATA_BRANCH}"
  else
    git checkout --orphan "${DATA_BRANCH}"
    git rm -rf . >/dev/null 2>&1 || true
  fi

  mkdir -p data reports
  # 22 方案：破坏性重建前先保留分支上现有的 picker 快照（只升不降合并的 keep 侧）
  for f in "${PICKER_FILES[@]}"; do
    if [[ -f "${f}" ]]; then
      mkdir -p "$(dirname "${picker_keep_dir}/${f}")"
      cp "${f}" "${picker_keep_dir}/${f}"
    fi
  done

  rm -rf data/france.md data/new_france.db data/source_health.json data/principal_capital_source_health.json data/principal_capital_sina_codes.json data/principal_capital_intraday_state.json data/principal_capital_intraday_state_shadow.json data/principal_capital_owner_conflict.json data/principal_capital_m5_audit.json data/snapshot_manifest.json reports
  rm -rf data/principal_capital_*_notified_*.json
  mkdir -p data reports
  cp -R "${snapshot_dir}/data/." data/
  cp -R "${snapshot_dir}/reports/." reports/

  # 22 方案：picker 快照只升不降合并（缺者补回、旧者不覆盖；并发 push 时取较新）
  python3 "${SCRIPT_DIR}/merge_picker_snapshots.py" "${picker_keep_dir}" "$(pwd)" "${PICKER_FILES[@]}"

  add_paths=(data/snapshot_manifest.json)
  for file in data/france.md data/new_france.db data/source_health.json data/principal_capital_source_health.json data/principal_capital_sina_codes.json data/principal_capital_intraday_state.json data/principal_capital_intraday_state_shadow.json data/principal_capital_owner_conflict.json data/principal_capital_m5_audit.json reports; do
    if [[ -e "${file}" ]]; then
      add_paths+=("${file}")
    fi
  done
  if compgen -G "data/principal_capital_*_notified_*.json" > /dev/null; then
    add_paths+=(data/principal_capital_*_notified_*.json)
  fi
  git add -f "${add_paths[@]}"

  if git diff --cached --quiet; then
    echo "No generated data changes to commit on ${DATA_BRANCH}"
    exit 0
  fi

  git commit -m "[bot] Daily data snapshot $(date -u +%Y-%m-%d)"

  for attempt in 1 2 3; do
    echo "Push attempt ${attempt}/3 to ${DATA_BRANCH}"
    git fetch origin "${DATA_BRANCH}" || true
    git pull --rebase origin "${DATA_BRANCH}" || true
    if git push origin "HEAD:${DATA_BRANCH}"; then
      exit 0
    fi
    sleep $((attempt * 2))
  done

  echo "推送 ${DATA_BRANCH} 失败"
  exit 1
)

exit $?
