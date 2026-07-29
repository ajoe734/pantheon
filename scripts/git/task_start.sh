#!/usr/bin/env bash
# Start a fresh per-task branch for the redesigned (post-2026-05-17) flow.
#
# Usage: scripts/git/task_start.sh <TASK-ID>
#
# Creates `task/<TASK-ID>` from origin/dev HEAD. Prints the recommended
# --index-file path for worker_commit.py. Refuses to clobber an existing
# task branch with uncommitted divergent work.
#
# Pairs with scripts/git/worker_commit.py and scripts/git/task_finalize.sh.

set -euo pipefail

TASK_ID="${1:-}"
if [[ -z "$TASK_ID" ]]; then
  echo "usage: $0 <TASK-ID>" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

CONFIG_FILE=".orchestrator/config.json"
DEV_BRANCH=$(python3 -c "
import json
try:
    c = json.load(open('$CONFIG_FILE'))
except Exception:
    c = {}
print(c.get('branch_workflow', {}).get('dev_branch') or 'dev')
")
PREFIX=$(python3 -c "
import json
try:
    c = json.load(open('$CONFIG_FILE'))
except Exception:
    c = {}
print(c.get('branch_workflow', {}).get('task_branch_prefix') or 'task/')
")
TASK_BRANCH="${PREFIX}${TASK_ID}"
INDEX_FILE="/tmp/git-index-task-${TASK_ID}"

echo "→ fetch origin $DEV_BRANCH"
git fetch origin "+refs/heads/${DEV_BRANCH}:refs/remotes/origin/${DEV_BRANCH}" --quiet

# If we're already on the target branch with unrelated dirty staging,
# abort to avoid sweeping someone else's index in.
CURRENT=$(git rev-parse --abbrev-ref HEAD)
if [[ "$CURRENT" == "$TASK_BRANCH" ]]; then
  if ! git diff --cached --quiet; then
    echo "ERROR: task branch already checked out with files staged." >&2
    echo "Refusing to clobber; clear the index first:" >&2
    echo "  git restore --staged --" >&2
    exit 2
  fi
fi

# Create (or move) the task branch to current dev tip.
git checkout -B "$TASK_BRANCH" "origin/${DEV_BRANCH}" --quiet

# Clear staging from any prior worker that may have left files in the
# shared .git/index. Idempotent and required by the closeout skill.
git restore --staged -- . 2>/dev/null || true

echo "✓ task branch '$TASK_BRANCH' is at $(git rev-parse --short HEAD) (origin/${DEV_BRANCH})"
echo
echo "Next steps:"
echo "  1. make your changes"
echo "  2. commit via worker_commit.py:"
echo "       python3 scripts/git/worker_commit.py \\"
echo "         --task-id '${TASK_ID}' \\"
echo "         --message-file /tmp/${TASK_ID}-msg.txt \\"
echo "         --scope <path1> <path2> ... \\"
echo "         --index-file '${INDEX_FILE}'"
echo "  3. push + open PR:"
echo "       ./scripts/git/task_finalize.sh '${TASK_ID}'"
