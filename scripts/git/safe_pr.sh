#!/usr/bin/env bash
# One-shot per-task PR creator. Bundles task_start + worker_commit +
# task_finalize into a single shell invocation so a Claude / autoworker
# session only has to make one Bash tool call (reducing exposure to the
# "tool result missing" transport bug seen during 2026-05-17 work).
#
# Usage:
#   scripts/git/safe_pr.sh <TASK-ID> \
#       --message-file /tmp/<TASK-ID>-msg.txt \
#       --scope <path1> [<path2> ...] \
#       [--reviewer <name>]                 # optional, defaults to "Codex"
#       [--no-pr]                           # commit + push, skip PR open
#       [--dry-run]
#
# Steps:
#   1. fetch origin dev
#   2. checkout -B task/<TASK-ID> origin/dev (or reuse if already on it)
#   3. git restore --staged -- .            (clear stale staging)
#   4. python3 scripts/git/worker_commit.py --task-id ... --scope ...
#      --message-file ... --index-file /tmp/git-index-task-<TASK-ID>
#   5. git push -u origin task/<TASK-ID>
#   6. gh pr create --base dev --head task/<TASK-ID>
#   7. resolve the canonical merge policy via
#      scripts/git/task_review_merge_gate.py, but leave auto-merge off for every
#      policy; for review-before-merge, the owner admits the exact PR/head/
#      manifest and the assigned reviewer approves that frozen delivery
#   8. wait for the canonical supervisor integration runner to merge before
#      running scripts/ai-status.sh done
#
# Output is intentionally short: each step prints a single PASS / FAIL line.
# Long sub-command output is captured into /tmp/safe-pr-<TASK-ID>.log so
# the Claude session does not have to relay multi-KB transcripts back
# through the tool channel.

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <TASK-ID> --message-file <path> --scope <p1> [<p2>...] [--reviewer N] [--no-pr] [--dry-run]" >&2
  exit 1
fi

TASK_ID="$1"; shift
MSG_FILE=""
SCOPE=()
REVIEWER=""
DO_PR=1
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --message-file) MSG_FILE="$2"; shift 2 ;;
    --scope)
      shift
      while [[ $# -gt 0 && "$1" != --* ]]; do
        SCOPE+=("$1"); shift
      done
      ;;
    --reviewer) REVIEWER="$2"; shift 2 ;;
    --no-pr) DO_PR=0; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$MSG_FILE" ]]; then
  echo "ERROR: --message-file is required" >&2
  exit 2
fi
if [[ ! -f "$MSG_FILE" ]]; then
  echo "ERROR: message file not found: $MSG_FILE" >&2
  exit 2
fi
if [[ ${#SCOPE[@]} -eq 0 ]]; then
  echo "ERROR: --scope must declare at least one path" >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

LOG="/tmp/safe-pr-${TASK_ID}.log"
: > "$LOG"

INDEX_FILE="/tmp/git-index-task-${TASK_ID}"
TASK_BRANCH="task/${TASK_ID}"

step() { printf '%-22s' "$1"; }
ok()   { echo "✓ $1"; }
fail() {
  echo "✗ $1"
  echo "  (last 20 lines of $LOG)" >&2
  tail -20 "$LOG" >&2
  exit 1
}

read_auto_merge_state() {
  local target="$1"
  local state
  if ! state=$(gh pr view "$target" --json autoMergeRequest --jq \
    'if .autoMergeRequest == null then "off" else "armed" end' 2>>"$LOG"); then
    return 1
  fi
  case "$state" in
    off|armed) printf '%s\n' "$state" ;;
    *)
      echo "unexpected autoMergeRequest state: $state" >>"$LOG"
      return 1
      ;;
  esac
}

lookup_existing_pr() {
  local result
  if ! result=$(gh pr list \
    --state open \
    --head "$TASK_BRANCH" \
    --base dev \
    --limit 2 \
    --json number \
    --jq 'if length == 0 then "" elif length == 1 then (.[0].number | tostring) else "AMBIGUOUS" end' \
    2>>"$LOG"); then
    return 1
  fi
  case "$result" in
    "") printf '\n' ;;
    AMBIGUOUS) return 2 ;;
    *[!0-9]*) return 1 ;;
    *) printf '%s\n' "$result" ;;
  esac
}

ensure_auto_merge_off() {
  local target="$1"
  local phase="$2"
  local state
  local revoke_rc=0
  if ! state=$(read_auto_merge_state "$target"); then
    fail "cannot verify autoMergeRequest $phase; refusing fail-open finalization"
  fi
  if [[ "$state" == "armed" ]]; then
    gh pr merge "$target" --disable-auto >>"$LOG" 2>&1 || revoke_rc=$?
    if ! state=$(read_auto_merge_state "$target"); then
      fail "cannot verify autoMergeRequest after revocation $phase"
    fi
    if [[ "$state" == "armed" ]]; then
      echo "auto-merge remains armed after revocation $phase (gh exit $revoke_rc)" >>"$LOG"
      fail "auto-merge remains armed; refusing fail-open finalization"
    fi
    ok "standing auto-merge request revoked and verified off $phase"
  else
    ok "auto-merge was already off $phase"
  fi
}

trap 'echo; echo "FAILED — see $LOG for full transcript"; exit 1' ERR

# --- 1. Fetch dev (short, just refs)
step "fetch dev"
if git fetch origin '+refs/heads/dev:refs/remotes/origin/dev' --quiet >>"$LOG" 2>&1; then ok "ok"; else fail "git fetch dev failed"; fi

# --- 2. Create / move task branch
step "checkout task branch"
if git checkout -B "$TASK_BRANCH" origin/dev >>"$LOG" 2>&1; then
  ok "$TASK_BRANCH @ $(git rev-parse --short HEAD)"
else
  fail "checkout failed"
fi

# --- 3. Clear any stale staging
step "reset staging"
git restore --staged -- . >>"$LOG" 2>&1 || true
ok "cleared"

# --- 4. Stage + commit via worker_commit.py
step "worker_commit"
if [[ "$DRY_RUN" -eq 1 ]]; then
  python3 scripts/git/worker_commit.py \
    --task-id "$TASK_ID" \
    --message-file "$MSG_FILE" \
    --scope "${SCOPE[@]}" \
    --index-file "$INDEX_FILE" \
    --dry-run >>"$LOG" 2>&1
  ok "dry-run: would commit ${#SCOPE[@]} scope entries"
  echo "(dry-run, skipping push / PR)"
  exit 0
fi
if python3 scripts/git/worker_commit.py \
     --task-id "$TASK_ID" \
     --message-file "$MSG_FILE" \
     --scope "${SCOPE[@]}" \
     --index-file "$INDEX_FILE" >>"$LOG" 2>&1; then
  ok "committed $(git rev-parse --short HEAD)"
else
  fail "worker_commit.py failed (likely scope leak or empty staging)"
fi

# --- 5. Resolve policy and revoke an existing PR before its head changes.
step "merge policy"
MERGE_POLICY=$(python3 scripts/git/task_review_merge_gate.py policy "$TASK_ID" 2>>"$LOG" | head -1 || true)
if [[ "$MERGE_POLICY" != "merge_then_review" ]]; then
  MERGE_POLICY="review_before_merge"
fi
ok "$MERGE_POLICY"

LOOKUP_RC=0
EXISTING_PR=$(lookup_existing_pr) || LOOKUP_RC=$?
if [[ "$LOOKUP_RC" -ne 0 ]]; then
  if [[ "$LOOKUP_RC" -eq 2 ]]; then
    fail "multiple open PRs target $TASK_BRANCH"
  fi
  fail "cannot resolve existing PR; refusing to push"
fi

if [[ -n "$EXISTING_PR" ]]; then
  step "pre-push revoke"
  ensure_auto_merge_off "$EXISTING_PR" "before push"
fi

# --- 6. Push
step "push task branch"
if git push -u origin "$TASK_BRANCH" >>"$LOG" 2>&1; then ok "pushed"; else fail "push failed"; fi

if [[ "$DO_PR" -eq 0 ]]; then
  if [[ -n "$EXISTING_PR" ]]; then
    step "post-push verify"
    ensure_auto_merge_off "$EXISTING_PR" "after push"
  fi
  echo "(--no-pr: skipping PR create)"
  exit 0
fi

# --- 7. Open PR (idempotent: reuse the unique PR resolved before push)
step "open PR"
PR_BODY=$(mktemp -t pr-body-XXXX.md)
git log -1 --format=%B HEAD > "$PR_BODY"
TITLE="$(git log -1 --format=%s HEAD)"

PR_CREATE_ARGS=(
  pr create
  --base dev
  --head "$TASK_BRANCH"
  --title "$TITLE"
  --body-file "$PR_BODY"
)
if [[ -n "$EXISTING_PR" ]]; then
  ok "PR #$EXISTING_PR already open"
else
  if gh "${PR_CREATE_ARGS[@]}" >>"$LOG" 2>&1; then
    EXISTING_PR=$(gh pr view "$TASK_BRANCH" --json number -q '.number' 2>/dev/null || echo "")
    if [[ -z "$EXISTING_PR" ]]; then
      fail "PR opened but its number cannot be resolved"
    fi
    ok "PR #$EXISTING_PR opened"
  else
    LOOKUP_RC=0
    EXISTING_PR=$(lookup_existing_pr) || LOOKUP_RC=$?
    if [[ "$LOOKUP_RC" -ne 0 || -z "$EXISTING_PR" ]]; then
      fail "gh pr create failed and no unique open PR can be resolved"
    fi
    ok "concurrent PR #$EXISTING_PR resolved after create returned nonzero"
  fi
fi

# --- 8. Submit to the sole merge owner. This helper may revoke a stale grant,
# but it never creates one and never issues a merge request.
step "submit integration"
ensure_auto_merge_off "$EXISTING_PR" "after push/open"
ok "PR #$EXISTING_PR left for canonical supervisor integration runner"

trap - ERR

echo
echo "DONE — task $TASK_ID on $TASK_BRANCH"
echo "  PR: https://github.com/ajoe734/pantheon/pull/${EXISTING_PR:-?}"
echo "  log: $LOG"
if [[ "$MERGE_POLICY" != "merge_then_review" ]]; then
  # Handoff freezes the PR/head/manifest before reviewer dispatch.
  echo "  next: owner admits this exact delivery for review with"
  echo "        AI_NAME=<owner> REVIEW_PR=${EXISTING_PR:-<pr-number>} REVIEW_HEAD_SHA=$(git rev-parse HEAD) \\"
  echo "          REVIEW_FILE=<repo-relative-evidence-manifest> \\"
  echo "          \"\$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh\" handoff $TASK_ID <reviewer> \"<ready for review>\""
  echo "        reviewer then approves the frozen delivery with"
  echo "          AI_NAME=<reviewer> \"\$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh\" approve $TASK_ID \"<review evidence>\""
  echo "        the canonical supervisor integration runner will then evaluate it"
else
  echo "  next: canonical supervisor integration runner evaluates merge-then-review policy"
fi
echo "  wait for the PR to merge before running scripts/ai-status.sh done"
