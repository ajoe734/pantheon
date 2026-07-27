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
#
# Merge authority comes from the canonical task contract, never from this
# helper. `scripts/git/task_review_merge_gate.py` resolves the policy:
#
#   review_before_merge (default, and forced for any task with an independent
#     reviewer) -- the PR is opened with auto-merge OFF. The exact assigned
#     reviewer approves the exact PR head, then
#     `scripts/git/auto_integrator.py --execute --task-id <TASK-ID>` merges it.
#
#   merge_then_review -- only when the canonical row declares it and requires
#     no independent review. `--auto --merge` is enabled as before so CI green
#     completes the PR.
#
# Do not run `scripts/ai-status.sh done` until GitHub reports the PR merged.

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

# Canonical merge policy. Any failure of the gate resolves to the gated
# default: this helper never widens merge authority on a broken read.
MERGE_POLICY=$(python3 scripts/git/task_review_merge_gate.py policy "$TASK_ID" 2>/dev/null | head -1 || true)
if [[ "$MERGE_POLICY" != "merge_then_review" ]]; then
  MERGE_POLICY="review_before_merge"
fi
echo "→ canonical merge policy: $MERGE_POLICY"

read_auto_merge_state() {
  local target="$1"
  local state
  if ! state=$(gh pr view "$target" --json autoMergeRequest --jq \
    'if .autoMergeRequest == null then "off" else "armed" end' 2>/dev/null); then
    return 1
  fi
  case "$state" in
    off|armed) printf '%s\n' "$state" ;;
    *) return 1 ;;
  esac
}

echo "→ open PR $TASK_BRANCH → $DEV_BRANCH"
PR_ARGS=(
  pr create
  --base "$DEV_BRANCH"
  --head "$TASK_BRANCH"
  --title "$CUSTOM_TITLE"
)
if [[ "$MERGE_POLICY" == "merge_then_review" ]]; then
  PR_ARGS+=(--label auto-merge)
fi
if [[ -n "$CUSTOM_BODY_FILE" ]]; then
  PR_ARGS+=(--body-file "$CUSTOM_BODY_FILE")
else
  PR_ARGS+=(--body "$CUSTOM_BODY")
fi
gh "${PR_ARGS[@]}"

if [[ "$MERGE_POLICY" == "merge_then_review" ]]; then
  echo "→ enable auto-merge (canonical contract permits merge-then-review)"
  gh pr merge "$TASK_BRANCH" --auto --merge
else
  # Fail closed against a stale auto-merge request left on this head by an
  # earlier run or by a hand-edited PR. Read back GitHub state because a
  # failed `--disable-auto` call can otherwise leave the merge grant armed.
  echo "→ auto-merge withheld until the assigned reviewer approves this exact head"
  if ! AUTO_MERGE_STATE=$(read_auto_merge_state "$TASK_BRANCH"); then
    echo "ERROR: cannot verify autoMergeRequest for $TASK_BRANCH; refusing fail-open finalization" >&2
    exit 4
  fi
  if [[ "$AUTO_MERGE_STATE" == "armed" ]]; then
    REVOKE_RC=0
    gh pr merge "$TASK_BRANCH" --disable-auto >/dev/null 2>&1 || REVOKE_RC=$?
    if ! AUTO_MERGE_STATE=$(read_auto_merge_state "$TASK_BRANCH"); then
      echo "ERROR: cannot verify autoMergeRequest after revocation; refusing fail-open finalization" >&2
      exit 4
    fi
    if [[ "$AUTO_MERGE_STATE" == "armed" ]]; then
      echo "ERROR: auto-merge remains armed after revocation (gh exit $REVOKE_RC); refusing finalization" >&2
      exit 4
    fi
    echo "✓ standing auto-merge request revoked and verified off"
  else
    echo "✓ auto-merge was already off"
  fi
fi

PR_URL=$(gh pr view "$TASK_BRANCH" --json url -q '.url' 2>/dev/null || echo "")
if [[ "$MERGE_POLICY" == "merge_then_review" ]]; then
  echo "✓ task $TASK_ID PR is open with auto-merge enabled"
else
  echo "✓ task $TASK_ID PR is open with auto-merge disabled (review before merge)"
fi
if [[ -n "$PR_URL" ]]; then
  echo "  $PR_URL"
fi
if [[ "$MERGE_POLICY" != "merge_then_review" ]]; then
  # The approval has to name this exact head: the gate compares the recorded
  # binding against the PR standing at merge time, so an unbound approval
  # cannot land the PR.
  PR_NUMBER=$(gh pr view "$TASK_BRANCH" --json number -q '.number' 2>/dev/null || echo "")
  HEAD_SHA=$(git rev-parse HEAD)
  echo "  next: assigned reviewer approves this exact head with"
  echo "        AI_NAME=<reviewer> REVIEW_PR=${PR_NUMBER:-<pr-number>} REVIEW_HEAD_SHA=$HEAD_SHA \\"
  echo "          \"\$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh\" approve $TASK_ID \"<review evidence>\""
  echo "        then python3 scripts/git/auto_integrator.py --execute --task-id $TASK_ID"
fi
echo "  (wait for the PR to merge before running scripts/ai-status.sh done)"
