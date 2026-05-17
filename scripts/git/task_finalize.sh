#!/usr/bin/env bash
# Push a task/<TASK-ID> branch and open its PR into dev with auto-merge.
#
# Usage: scripts/git/task_finalize.sh <TASK-ID> [--title <title>] [--body <body>] [--body-file <path>]
#
# Defaults:
#   * Title  = HEAD commit subject
#   * Body   = HEAD commit body (everything after the subject line)
#   * Labels = auto-merge
#
# Refuses to push if the local task branch is not ahead of origin/dev or
# if the branch name doesn't follow the task/<TASK-ID> convention.
# Adds `--auto --merge` so the PR completes automatically when the dev
# branch protection's required status checks (Commit trailers / Runtime
# mirror guard / Smoke acceptance) turn green.

set -euo pipefail

TASK_ID="${1:-}"
if [[ -z "$TASK_ID" ]]; then
  echo "usage: $0 <TASK-ID> [--title T] [--body B | --body-file F]" >&2
  exit 1
fi
shift

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

CUSTOM_TITLE=""
CUSTOM_BODY=""
CUSTOM_BODY_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --title) CUSTOM_TITLE="$2"; shift 2 ;;
    --body) CUSTOM_BODY="$2"; shift 2 ;;
    --body-file) CUSTOM_BODY_FILE="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

CURRENT=$(git rev-parse --abbrev-ref HEAD)
if [[ "$CURRENT" != "$TASK_BRANCH" ]]; then
  echo "ERROR: not on $TASK_BRANCH (currently on $CURRENT)" >&2
  exit 2
fi

git fetch origin "$DEV_BRANCH" --quiet
AHEAD=$(git rev-list --count "origin/${DEV_BRANCH}..HEAD")
if [[ "$AHEAD" -eq 0 ]]; then
  echo "ERROR: $TASK_BRANCH has no commits ahead of origin/${DEV_BRANCH}; nothing to PR." >&2
  exit 3
fi

echo "→ push $TASK_BRANCH ($AHEAD commits ahead of origin/${DEV_BRANCH})"
git push -u origin "$TASK_BRANCH"

# Compose title / body
if [[ -z "$CUSTOM_TITLE" ]]; then
  CUSTOM_TITLE=$(git log -1 --format=%s HEAD)
fi
if [[ -z "$CUSTOM_BODY" && -z "$CUSTOM_BODY_FILE" ]]; then
  CUSTOM_BODY_FILE=$(mktemp -t task-pr-body-XXXX.md)
  git log --format='%b' "origin/${DEV_BRANCH}..HEAD" > "$CUSTOM_BODY_FILE"
fi

echo "→ open PR $TASK_BRANCH → $DEV_BRANCH"
PR_ARGS=(
  pr create
  --base "$DEV_BRANCH"
  --head "$TASK_BRANCH"
  --title "$CUSTOM_TITLE"
  --label auto-merge
)
if [[ -n "$CUSTOM_BODY_FILE" ]]; then
  PR_ARGS+=(--body-file "$CUSTOM_BODY_FILE")
else
  PR_ARGS+=(--body "$CUSTOM_BODY")
fi
gh "${PR_ARGS[@]}"

echo "→ enable auto-merge"
gh pr merge "$TASK_BRANCH" --auto --merge

PR_URL=$(gh pr view "$TASK_BRANCH" --json url -q '.url' 2>/dev/null || echo "")
echo "✓ task $TASK_ID PR is open with auto-merge enabled"
if [[ -n "$PR_URL" ]]; then
  echo "  $PR_URL"
fi
echo "  (PR will merge once branch-ci status checks turn green)"
