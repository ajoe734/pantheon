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
#   6. gh pr create --base dev --head task/<TASK-ID> --label auto-merge
#   7. gh pr merge task/<TASK-ID> --auto --merge
#   8. wait for the PR to merge before running scripts/ai-status.sh done
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

trap 'echo; echo "FAILED — see $LOG for full transcript"; exit 1' ERR

# --- 1. Fetch dev (short, just refs)
step "fetch dev"
if git fetch origin \
  "+refs/heads/dev:refs/remotes/origin/dev" \
  --quiet >>"$LOG" 2>&1; then ok "ok"; else fail "git fetch dev failed"; fi

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

# --- 5. Push
step "push task branch"
if git push -u origin "$TASK_BRANCH" >>"$LOG" 2>&1; then ok "pushed"; else fail "push failed"; fi

if [[ "$DO_PR" -eq 0 ]]; then
  echo "(--no-pr: skipping PR create + auto-merge)"
  exit 0
fi

# --- 6. Open PR (idempotent: skip if one already open)
step "open PR"
PR_BODY=$(mktemp -t pr-body-XXXX.md)
git log -1 --format=%B HEAD > "$PR_BODY"
TITLE="$(git log -1 --format=%s HEAD)"

EXISTING_PR=$(gh pr list --head "$TASK_BRANCH" --state open --json number -q '.[0].number' 2>/dev/null || echo "")
if [[ -n "$EXISTING_PR" ]]; then
  ok "PR #$EXISTING_PR already open; will enable auto-merge"
else
  if gh pr create \
       --base dev \
       --head "$TASK_BRANCH" \
       --title "$TITLE" \
       --body-file "$PR_BODY" \
       --label auto-merge >>"$LOG" 2>&1; then
    EXISTING_PR=$(gh pr list --head "$TASK_BRANCH" --state open --json number -q '.[0].number' 2>/dev/null || echo "")
    ok "PR #$EXISTING_PR opened"
  else
    fail "gh pr create failed"
  fi
fi

# --- 7. Enable auto-merge (non-fatal if repo doesn't allow it)
step "enable auto-merge"
if gh pr merge "$EXISTING_PR" --auto --merge >>"$LOG" 2>&1; then
  ok "auto-merge enabled on PR #$EXISTING_PR"
else
  echo "⚠ auto-merge not enabled (check repo setting `allow_auto_merge`); PR is still open"
fi

trap - ERR

echo
echo "DONE — task $TASK_ID on $TASK_BRANCH"
echo "  PR: https://github.com/ajoe734/pantheon/pull/${EXISTING_PR:-?}"
echo "  log: $LOG"
echo "  wait for the PR to merge before running scripts/ai-status.sh done"
