# Review — OPS-STATUS-COMMAND-RUNTIME-PIN-001 (PR #3783)

Reviewer: Claude · Verdict: **APPROVED (code level)** · 2026-07-17

## Verdict

Approved at head `aea3ad460` (this branch merged with `origin/dev` to clear
a BEHIND merge state and refresh the push-event commit range; no functional
changes since the prior review at `3d139dbe3`). Postmerge cutover (drain
pre-pin workers, install the merged SHA, reissue leases, re-run stale-worktree
proof) is still outstanding per the task's own operational-cutover
requirement and is not claimed complete by this approval.

## Verified

- Read the command-root/SHA validation logic in `.orchestrator/common.py`,
  `scripts/ai_status.py` (`validate_status_command_runtime_binding`,
  `validate_active_status_command_lease`), `scripts/ai-status.sh`
  (`validate_command_root`), and `.orchestrator/worker_runner.py`
  (`validate_status_command_runtime`). All four fail closed before any
  canonical mutation on: missing root, relative root, symlink component,
  non-git-root/nested repo, SHA mismatch, remote mismatch, unmerged-into-
  `origin/dev` SHA.
- `validate_active_status_command_lease` checks worker existence, active
  status, lease expiry, task identity (argv/env/worker agreement),
  status-root match, workspace match, issued-vs-running command runtime
  match, and worktree-lease match — all under a shared `runtime_state_lock`
  held across the subsequent exclusive `canonical_task_state_lock` mutation,
  closing the race window where a lease could be invalidated mid-mutation.
- `start_worker_for_request` issues `status_command_runtime` into
  `request.metadata` for every new dispatch; `resume_claude_worker` and
  `worker_runner.py` propagate/re-validate the same runtime for resumed and
  child-process paths. No bypassed dispatch path found.
- Ran from this exact worktree/head (`aea3ad460`, after merging
  `origin/dev`):
  - `python3 scripts/test_status_command_runtime_pin.py` -> 6/6 OK.
  - `python3 .orchestrator/test_common.py` -> 64/64 OK.
  - `python3 .orchestrator/test_supervisor.py` -> 277/277 OK.
  - `bash -n scripts/ai-status.sh scripts/sync-dev-root.sh` -> OK.
  - `python3 -m py_compile scripts/ai_status.py .orchestrator/common.py
    .orchestrator/supervisor.py .orchestrator/worker_runner.py
    scripts/test_status_command_runtime_pin.py` -> OK.
  - `git diff --check origin/dev...HEAD` -> OK.

## Fixed by this review pass

- PR was `BEHIND` `origin/dev`; merged `origin/dev` into the task branch
  (no conflicts, `mergeable: MERGEABLE` before and after).
- `Commit trailers` check was `FAILURE` on the push-event range only,
  because that range pulled in an unowned, already-merged `origin/dev`
  commit whose subject exceeded the 72-char trailer-gate limit. Merging
  `origin/dev` again refreshes the push-event range to exclude that commit
  (known push-event-range false positive, not a defect in this task's own
  commits).

## Scope

Diff stays within the owned artifact list and does not touch product
trading behavior, frontend, BFF product routes, or historical
activity/archive bytes.

## Not yet closed

Per the task's own acceptance criteria, `done` still requires the
postmerge cutover gate (drain pre-pin workers, install the merged SHA,
reissue leases, re-run stale-worktree positive/negative proof) documented
in `docs/deployment/evidence/ops-status-command-runtime-pin-001/README.md`.
This approval covers the code/tests, not the operational cutover.
