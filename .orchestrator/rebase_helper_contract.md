# rebase_helper — Contract

Module: `.orchestrator/rebase_helper.py`
Task: OPS-REBASE-AUTO-001
Phase: Sprint 7 / EPIC-OPS-BACKLOG

## Problem

When a background worker runs `git pull --rebase` and one or more of its
commits are already applied on the target branch, git stops the rebase with
an "empty" commit state and waits for operator input.  This stalls the
approval-queue and blocks subsequent dispatch cycles.

## Solution

`continue_or_skip_empty(repo_path)` detects an in-progress rebase and
drives it to completion by:

- skipping commits whose changeset is empty (already applied upstream), and
- aborting immediately when a real merge conflict is detected.

The function is safe to call every supervisor loop; it is a no-op when no
rebase is in progress.

## Public API

### `continue_or_skip_empty(repo_path: str | Path) -> RebaseResult`

| Field     | Type | Values |
|-----------|------|--------|
| `action`  | str  | `"continued"` · `"skipped"` · `"aborted_with_conflict"` · `"no_rebase"` |
| `skipped` | int  | number of empty commits auto-skipped |
| `message` | str  | human-readable summary |

**action semantics:**

| Value | Meaning |
|-------|---------|
| `continued` | Rebase was in progress; completed without skipping any commits. |
| `skipped` | Rebase completed; ≥ 1 empty commits were auto-skipped. |
| `aborted_with_conflict` | Real (non-empty) conflict detected; `git rebase --abort` was run. |
| `no_rebase` | No rebase was in progress; function returned immediately. |

## Detection strategy

| Condition | Check |
|-----------|-------|
| Rebase in progress | `git rev-parse --git-dir` then `rebase-merge` or `rebase-apply` exists; falls back to `.git` / `.git` file parsing |
| Real conflict present | `git status --porcelain` contains `UU`, `AA`, `DD`, or any `U?`/`?U` prefix |
| Commit would be empty | `git diff --cached --quiet` exits 0 (nothing staged) |

## Safety

- Maximum 200 loop iterations per call; rebase is aborted if the limit is
  reached and `aborted_with_conflict` is returned.
- `GIT_EDITOR=true` is set when calling `--continue` so git never blocks
  waiting for an editor.
- Failed `--skip` or `--continue` commands abort the rebase and return
  `aborted_with_conflict` with git's diagnostic text.

## Supervisor integration

`supervisor.py` imports `continue_or_skip_empty` from this module and calls
it once at the top of each `run_once` loop iteration (before task dispatch),
passing `THIS_DIR.parent` as `repo_path`.  The single-line call resolves any
stalled rebase before workers are dispatched.

## Invariants

- This module does **not** start a rebase; it only resolves one already in
  progress.
- No credentials, no network I/O, no write to `ai-status.json` or any
  canonical state file.
- All subprocess calls use `capture_output=True` to keep output off the
  supervisor's stdout.
