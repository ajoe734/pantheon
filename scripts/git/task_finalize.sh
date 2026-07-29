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

git fetch origin "+refs/heads/${DEV_BRANCH}:refs/remotes/origin/${DEV_BRANCH}" --quiet
AHEAD=$(git rev-list --count "origin/${DEV_BRANCH}..HEAD")
if [[ "$AHEAD" -eq 0 ]]; then
  echo "ERROR: $TASK_BRANCH has no commits ahead of origin/${DEV_BRANCH}; nothing to PR." >&2
  exit 3
fi

# Canonical merge policy. Any failure of the gate resolves to the gated
# default. Resolve it before the push because a standing auto-merge request on
# an existing PR can otherwise survive the head change and land the new head
# before this helper reaches its post-push checks.
MERGE_POLICY=$(python3 scripts/git/task_review_merge_gate.py policy "$TASK_ID" 2>/dev/null | head -1 || true)
if [[ "$MERGE_POLICY" != "merge_then_review" ]]; then
  MERGE_POLICY="review_before_merge"
fi
echo "→ canonical merge policy: $MERGE_POLICY"

lookup_existing_pr() {
  local result
  if ! result=$(gh pr list \
    --state open \
    --head "$TASK_BRANCH" \
    --base "$DEV_BRANCH" \
    --limit 2 \
    --json number \
    --jq 'if length == 0 then "" elif length == 1 then (.[0].number | tostring) else "AMBIGUOUS" end' \
    2>/dev/null); then
    return 1
  fi
  case "$result" in
    "") printf '\n' ;;
    AMBIGUOUS) return 2 ;;
    *[!0-9]*) return 1 ;;
    *) printf '%s\n' "$result" ;;
  esac
}

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

ensure_auto_merge_off() {
  local target="$1"
  local phase="$2"
  local state
  local revoke_rc=0
  if ! state=$(read_auto_merge_state "$target"); then
    echo "ERROR: cannot verify autoMergeRequest $phase; refusing fail-open finalization" >&2
    return 1
  fi
  if [[ "$state" == "armed" ]]; then
    gh pr merge "$target" --disable-auto >/dev/null 2>&1 || revoke_rc=$?
    if ! state=$(read_auto_merge_state "$target"); then
      echo "ERROR: cannot verify autoMergeRequest after revocation $phase; refusing fail-open finalization" >&2
      return 1
    fi
    if [[ "$state" == "armed" ]]; then
      echo "ERROR: auto-merge remains armed after revocation $phase (gh exit $revoke_rc); refusing finalization" >&2
      return 1
    fi
    echo "✓ standing auto-merge request revoked and verified off $phase"
  else
    echo "✓ auto-merge was already off $phase"
  fi
}

LOOKUP_RC=0
EXISTING_PR=$(lookup_existing_pr) || LOOKUP_RC=$?
if [[ "$LOOKUP_RC" -ne 0 ]]; then
  if [[ "$LOOKUP_RC" -eq 2 ]]; then
    echo "ERROR: multiple open PRs target $TASK_BRANCH; refusing ambiguous finalization" >&2
  else
    echo "ERROR: cannot resolve the existing PR for $TASK_BRANCH; refusing to push" >&2
  fi
  exit 4
fi

if [[ "$MERGE_POLICY" == "review_before_merge" && -n "$EXISTING_PR" ]]; then
  echo "→ revoke any standing merge grant before changing existing PR #$EXISTING_PR"
  ensure_auto_merge_off "$EXISTING_PR" "before push" || exit 4
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
)
if [[ "$MERGE_POLICY" == "merge_then_review" ]]; then
  PR_ARGS+=(--label auto-merge)
fi
if [[ -n "$CUSTOM_BODY_FILE" ]]; then
  PR_ARGS+=(--body-file "$CUSTOM_BODY_FILE")
else
  PR_ARGS+=(--body "$CUSTOM_BODY")
fi

if [[ -n "$EXISTING_PR" ]]; then
  echo "✓ PR #$EXISTING_PR already open"
else
  if ! gh "${PR_ARGS[@]}"; then
    # A concurrent creator may have won after the pre-push lookup. Continue
    # only when a new unique PR can now be resolved; ambiguity stays closed.
    LOOKUP_RC=0
    EXISTING_PR=$(lookup_existing_pr) || LOOKUP_RC=$?
    if [[ "$LOOKUP_RC" -ne 0 || -z "$EXISTING_PR" ]]; then
      echo "ERROR: gh pr create failed and no unique open PR can be resolved" >&2
      exit 4
    fi
    echo "✓ concurrent PR #$EXISTING_PR resolved after create returned nonzero"
  fi
fi

PR_TARGET="${EXISTING_PR:-$TASK_BRANCH}"

if [[ "$MERGE_POLICY" == "merge_then_review" ]]; then
  echo "→ enable auto-merge (canonical contract permits merge-then-review)"
  gh pr merge "$PR_TARGET" --auto --merge
else
  # Re-read after the push/create too. The pre-push revocation closes the
  # existing-PR race; this second proof catches a concurrent re-arm.
  echo "→ auto-merge withheld until the assigned reviewer approves this exact head"
  ensure_auto_merge_off "$PR_TARGET" "after push/open" || exit 4
fi

PR_URL=$(gh pr view "$PR_TARGET" --json url -q '.url' 2>/dev/null || echo "")
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
  PR_NUMBER=$(gh pr view "$PR_TARGET" --json number -q '.number' 2>/dev/null || echo "")
  HEAD_SHA=$(git rev-parse HEAD)
  echo "  next: assigned reviewer approves this exact head with"
  echo "        AI_NAME=<reviewer> REVIEW_PR=${PR_NUMBER:-<pr-number>} REVIEW_HEAD_SHA=$HEAD_SHA \\"
  echo "          \"\$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh\" approve $TASK_ID \"<review evidence>\""
  echo "        then python3 scripts/git/auto_integrator.py --execute --task-id $TASK_ID"
fi
echo "  (wait for the PR to merge before running scripts/ai-status.sh done)"
