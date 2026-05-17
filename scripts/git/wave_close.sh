#!/usr/bin/env bash
# Wave-close helper (chair-review).
#
# Usage: scripts/git/wave_close.sh <wave-id>          # e.g. 2026-W21
#
# Merges wave/<wave-id> into dev with --no-ff, archives the wave branch,
# cuts publish/v<YYYY>.<WW>.0 from dev, and tags release/<VER>.
# Runs inside an isolated worktree to avoid orchestrator write competition.

set -euo pipefail

WAVE_ID="${1:-}"
if [[ -z "$WAVE_ID" ]]; then
  echo "usage: $0 <wave-id>  (e.g. 2026-W21)" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

CONFIG_FILE=".orchestrator/config.json"
if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "missing $CONFIG_FILE" >&2
  exit 2
fi

DEV_BRANCH=$(python3 -c "import json; c=json.load(open('$CONFIG_FILE')); print(c.get('wave_workflow',{}).get('dev_branch','dev'))")
WAVE_BRANCH="wave/$WAVE_ID"

YEAR="${WAVE_ID%%-W*}"
WEEK="${WAVE_ID##*-W}"
VER="v${YEAR}.${WEEK}.0"
PUBLISH_BRANCH="publish/$VER"
RELEASE_TAG="release/$VER"
ARCHIVE_TAG="archive/wave-${WAVE_ID}-$(date +%F)"

WORKTREE_DIR="$(mktemp -d -t pantheon-wave-close-XXXXXX)"
cleanup() {
  if [[ -d "$WORKTREE_DIR/.git" || -d "$WORKTREE_DIR" ]]; then
    git worktree remove --force "$WORKTREE_DIR" 2>/dev/null || rm -rf "$WORKTREE_DIR"
  fi
}
trap cleanup EXIT

echo "→ fetch origin"
git fetch origin --prune

echo "→ run wave acceptance"
"$ROOT_DIR/scripts/run-acceptance.sh" wave "origin/$WAVE_BRANCH"

echo "→ isolated worktree at $WORKTREE_DIR"
git worktree add "$WORKTREE_DIR" "origin/$DEV_BRANCH" >/dev/null
cd "$WORKTREE_DIR"

git checkout -B "$DEV_BRANCH" "origin/$DEV_BRANCH"

echo "→ merge $WAVE_BRANCH into $DEV_BRANCH"
git merge --no-ff "origin/$WAVE_BRANCH" -m "wave-close: $WAVE_ID"

echo "→ push $DEV_BRANCH"
git push origin "$DEV_BRANCH"

echo "→ cut $PUBLISH_BRANCH"
git checkout -B "$PUBLISH_BRANCH"
git push -u origin "$PUBLISH_BRANCH"

echo "→ tag $RELEASE_TAG and $ARCHIVE_TAG"
git tag -a "$RELEASE_TAG" -m "dev publish: wave $WAVE_ID"
git tag -a "$ARCHIVE_TAG" "origin/$WAVE_BRANCH" -m "wave $WAVE_ID merged into $DEV_BRANCH as $RELEASE_TAG"
git push origin "$RELEASE_TAG" "$ARCHIVE_TAG"

echo "→ delete remote $WAVE_BRANCH"
git push origin --delete "$WAVE_BRANCH" || true

cd "$ROOT_DIR"
python3 scripts/ai_status.py wave close "$WAVE_ID" >/dev/null

echo "✓ wave $WAVE_ID closed; published as $RELEASE_TAG"
