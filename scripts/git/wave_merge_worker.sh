#!/usr/bin/env bash
# Merge a worker branch into the current wave (chair-review or auto-merge bot).
#
# Usage: scripts/git/wave_merge_worker.sh <worker-name>
#
# Looks up the worker's branch from .orchestrator/config.json, merges it with
# --no-ff into the current wave/<id>, pushes. Refuses if there is no current
# wave or the worker's commits are not present on its branch.

set -euo pipefail

WORKER_NAME="${1:-}"
if [[ -z "$WORKER_NAME" ]]; then
  echo "usage: $0 <worker-name>  (e.g. Claude)" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

CONFIG_FILE=".orchestrator/config.json"
STATUS_FILE="ai-status.json"

WAVE_ID=$(python3 -c "
import json, sys
try:
    s = json.load(open('$STATUS_FILE'))
except FileNotFoundError:
    sys.exit('missing $STATUS_FILE')
w = (s.get('wave_state') or {}).get('current_wave_id')
if not w:
    sys.exit('no current wave; run wave_open.sh first')
print(w)
")
WAVE_BRANCH="wave/$WAVE_ID"

WORKER_BRANCH=$(python3 -c "
import json, sys
c = json.load(open('$CONFIG_FILE'))
b = (c.get('wave_workflow') or {}).get('worker_branches', {}).get('$WORKER_NAME')
if not b:
    sys.exit('worker $WORKER_NAME not configured in wave_workflow.worker_branches')
print(b)
")

echo "→ fetch origin"
git fetch origin --prune

if ! git ls-remote --exit-code --heads origin "$WORKER_BRANCH" >/dev/null 2>&1; then
  echo "worker branch $WORKER_BRANCH does not exist on origin" >&2
  exit 3
fi

LAST_COMMIT_SUBJECT=$(git log -1 --format=%s "origin/$WORKER_BRANCH")
echo "→ merging $WORKER_BRANCH into $WAVE_BRANCH (last: $LAST_COMMIT_SUBJECT)"

WORKTREE_DIR="$(mktemp -d -t pantheon-wave-merge-XXXXXX)"
cleanup() {
  git worktree remove --force "$WORKTREE_DIR" 2>/dev/null || rm -rf "$WORKTREE_DIR"
}
trap cleanup EXIT

git worktree add "$WORKTREE_DIR" "origin/$WAVE_BRANCH" >/dev/null
cd "$WORKTREE_DIR"
git checkout -B "$WAVE_BRANCH" "origin/$WAVE_BRANCH"
git merge --no-ff "origin/$WORKER_BRANCH" \
  -m "wave-merge: $WORKER_NAME — $LAST_COMMIT_SUBJECT"
git push origin "$WAVE_BRANCH"

echo "✓ merged $WORKER_NAME into $WAVE_BRANCH"
