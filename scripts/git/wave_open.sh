#!/usr/bin/env bash
# Wave-open helper (chair-review).
#
# Usage: scripts/git/wave_open.sh <wave-id>          # e.g. 2026-W21
#
# Cuts wave/<wave-id> from origin/dev, pushes it, registers the wave with
# the orchestrator, and resets every worker/* branch to the new wave head.
#
# Safe to re-run: idempotent on the wave branch; refuses if a different wave
# is still recorded as the current wave.

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
  echo "missing $CONFIG_FILE; copy from config.example.json" >&2
  exit 2
fi

DEV_BRANCH=$(python3 -c "import json; c=json.load(open('$CONFIG_FILE')); print(c.get('wave_workflow',{}).get('dev_branch','dev'))")
WAVE_BRANCH="wave/$WAVE_ID"

echo "→ fetch origin"
git fetch origin --prune

echo "→ register wave $WAVE_ID with orchestrator"
python3 scripts/ai_status.py wave open "$WAVE_ID" >/dev/null

if git ls-remote --exit-code --heads origin "$WAVE_BRANCH" >/dev/null 2>&1; then
  echo "→ wave branch $WAVE_BRANCH already exists on origin; reusing"
  git fetch origin "$WAVE_BRANCH":"$WAVE_BRANCH" 2>/dev/null || true
else
  echo "→ creating $WAVE_BRANCH from origin/$DEV_BRANCH"
  git branch -f "$WAVE_BRANCH" "origin/$DEV_BRANCH"
  git push -u origin "$WAVE_BRANCH"
fi

# Reset every configured worker branch to the wave head.
mapfile -t WORKERS < <(python3 -c '
import json
c = json.load(open(".orchestrator/config.json"))
for name, branch in c.get("wave_workflow", {}).get("worker_branches", {}).items():
    print(f"{name}|{branch}")
')

if [[ ${#WORKERS[@]} -eq 0 ]]; then
  echo "no worker_branches configured; skipping worker reset" >&2
else
  for entry in "${WORKERS[@]}"; do
    name="${entry%%|*}"
    branch="${entry#*|}"
    echo "→ reset $branch ($name) → $WAVE_BRANCH"
    if git ls-remote --exit-code --heads origin "$branch" >/dev/null 2>&1; then
      git push --force-with-lease origin "$WAVE_BRANCH:refs/heads/$branch"
    else
      git push origin "$WAVE_BRANCH:refs/heads/$branch"
    fi
  done
fi

echo "✓ wave $WAVE_ID opened on $WAVE_BRANCH; ${#WORKERS[@]} worker branches synced"
