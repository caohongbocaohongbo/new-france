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

snapshot_dir="$(mktemp -d)"
cleanup() {
  rm -rf "${snapshot_dir}"
}
trap cleanup EXIT

mkdir -p "${snapshot_dir}/data" "${snapshot_dir}/reports"

for file in data/france.md data/new_france.db data/source_health.json; do
  if [[ -f "${file}" ]]; then
    cp "${file}" "${snapshot_dir}/${file}"
  fi
done

if [[ -d reports ]]; then
  cp -R reports/. "${snapshot_dir}/reports/"
fi

cat > "${snapshot_dir}/data/snapshot_manifest.json" <<EOF
{
  "source_branch": "${BASE_BRANCH}",
  "source_sha": "${HEAD_SHA}",
  "generated_at_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "data_branch": "${DATA_BRANCH}"
}
EOF

if git show-ref --verify --quiet "refs/remotes/origin/${DATA_BRANCH}"; then
  git checkout -B "${DATA_BRANCH}" "origin/${DATA_BRANCH}"
else
  git checkout --orphan "${DATA_BRANCH}"
  git rm -rf . >/dev/null 2>&1 || true
fi

mkdir -p data reports
rm -rf data/france.md data/new_france.db data/source_health.json data/snapshot_manifest.json reports
mkdir -p data reports
cp -R "${snapshot_dir}/data/." data/
cp -R "${snapshot_dir}/reports/." reports/

git add -f data/france.md data/new_france.db data/source_health.json data/snapshot_manifest.json reports/ 2>/dev/null || true

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
