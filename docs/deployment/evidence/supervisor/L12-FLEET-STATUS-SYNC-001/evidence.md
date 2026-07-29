# L12-FLEET-STATUS-SYNC-001 closeout evidence

Status: owner evidence ready for independent Antigravity review

## Outcome

The status/dashboard drift detector is already merged. Pantheon PR
[#4282](https://github.com/ajoe734/pantheon/pull/4282) delivered exact head
`e806affaa279f8b9d4b41bae6117a9431c99b90e` to `dev` as merge commit
`a0020c5ac50e510467a5e80c412c7703245cf4dd` at
`2026-07-27T22:24:51Z`. Both commits are ancestors of current `origin/dev`.
This closeout does not restart or modify that implementation.

PR #4282 added two high-severity dashboard truth mismatches:

- `delivery_merged_needs_closeout` when a non-terminal task has structured or
  recognized textual evidence that its delivery already merged; and
- `delivery_binding_stale` when `source_ref.head_sha` disagrees with later
  exact-head review or merge evidence.

The matching resolution hints tell operators to create formal closeout evidence
or align the exact-head binding instead of restarting an already-merged task.

## Live truth snapshot

At `2026-07-28T18:45:41Z`, governed `ai-status.sh show`, generated
`current-work.md`, and the dashboard worker/task link all identified
`L12-FLEET-STATUS-SYNC-001` as an active `in_progress` task owned by Codex with
Codex2 as reviewer. The status-root and docs-site copies of `ai-status.json`,
`current-work.md`, and `dashboard-bundle.json` had matching SHA-256 digests.

The live row still has legacy top-level dispatch metadata:

- `source_pr`: PR #4277
- `source_head`: `2a2d6b7c50d60a940a891d9e18e0ea8a1e7a961e`

Those fields are not the structured `source_ref.head_sha` consumed by the
dashboard. The dashboard therefore normalizes this task's `source_ref` to `{}`:
it does not present the old head as the current delivery binding. The task title,
governed progress note, and this manifest explicitly identify PR #4282's exact
head and merge commit.

This distinction matters for review. PR #4282 proves detection for structured
`source_ref` and structured/textual merged-delivery evidence; it does not
migrate every legacy top-level `source_pr`/`source_head` field. No broader claim
is made here.

## Current resume verification

The prior snapshot remains historical evidence, but it is no longer sufficient
for closeout. The owner's first 2026-07-29 refresh attempt correctly failed
closed because the supervisor-issued runtime SHA was
`8ea01a8e3993b3dabc6cd475c7058d299eaf4a01`, while the command root had advanced
to `352e8172c1d5a32555216ef54c5557042bdfce1f`. The owner did not override that
binding or run a task-worktree status command.

Supervisor then issued a fresh binding at
`e7eab746afc8ad09321c6da69263dbda4d5eccce`, exactly matching the governed
command root. At `2026-07-29T10:54:09Z`, the owner ran the governed `progress`
transaction. The centralized status root at `/home/lupin/pantheon` then
reported:

- `ai-status.json`: one active matching task row;
- `current-work.md`: one active matching task row; and
- `dashboard-bundle.json`: one active worker/task link with owner Codex,
  reviewer Antigravity, `task_source=active`, `task_status=in_progress`, and no
  mismatch flags.

Each status-root file is byte-identical to its docs-site mirror. The matching
SHA-256 digests are:

- `ai-status.json`: `28067ac3924310e578d301bdc0a32e54444c3455a06c004e08ba5e69d7556ea4`;
- `current-work.md`: `ba9ba0320f7f3dd53eda6790421a2afd189e4ee01693ec0f6f6a930f3b9820f1`;
- `dashboard-bundle.json`: `6e4530557060c82b84f00669452317b987c4e59f377516c604dfb399258f0a27`.

The command-runtime checkout is the governed executable source, not the
centralized status root. AC1 is now satisfied.

## Acceptance mapping

1. Governed show and generated views agree on task existence: pass after the
   fresh supervisor-bound transaction. The active row exists in `ai-status`,
   `current-work`, and the dashboard, and all three docs-site mirrors match.
2. File-only ghost state cannot outrank the authoritative journal: pass.
   `test_authoritative_load_ignores_divergent_file_and_save_advances_journal`
   verifies that a rogue task written only to `ai-status.json` is ignored, and
   `test_derived_views_skip_a_stale_projection` prevents that stale state from
   regenerating derived views.
3. Stale delivery binding is explicit: pass with the legacy-input note above.
   The two PR #4282 regressions prove stale structured exact-head detection and
   non-terminal merged-delivery detection. The live legacy fields are not
   exposed as dashboard `source_ref`; the exact delivery is explicit in the
   task record and evidence.
4. Assignment survives authoritative projection refresh: pass.
   `test_authoritative_bridge_dispatch_survives_next_projection_cycle` proves
   that the journal-backed bridge assignment returns after a stale file-only
   projection.
5. Closeout does not restart implementation: pass pending independent review.
   This branch still changes only the task brief and task-scoped evidence.

## Verification

The original implementation record reports:

```text
/home/lupin/pantheon/.venv/bin/python -m pytest -q \
  scripts/test_ai_status.py \
  scripts/test_status_file_guard.py \
  scripts/test_supervisor_runtime_health.py

173 passed, 31 subtests passed
```

Owner rerun on the current task worktree:

```text
python3 scripts/dev/provision_python_distribution.py
PASS

env -u PANTHEON_COMMAND_ROOT \
    -u PANTHEON_COMMAND_RUNTIME_SHA \
    -u PANTHEON_STATUS_COMMAND_ROOT \
    -u PANTHEON_STATUS_COMMAND_SHA \
  .venv-pantheon/bin/python3 -m pytest -q \
  scripts/test_ai_status.py scripts/test_status_file_guard.py
168 passed, 31 subtests passed in 26.65s at the refreshed PR head

.venv-pantheon/bin/python3 -m pytest -q scripts/test_ai_status.py \
  -k 'dashboard_flags_merged_delivery_that_still_needs_closeout or
      dashboard_uses_structured_delivery_evidence_for_closeout_drift or
      derived_views_skip_a_stale_projection or
      authoritative_load_ignores_divergent_file_and_save_advances_journal'
4 passed, 148 deselected in 0.65s

.venv-pantheon/bin/python3 -m pytest -q \
  services/control-plane/bff/assistant/tests/test_dev_bridge_reliability.py \
  -k authoritative_bridge_dispatch_survives_next_projection_cycle
1 passed, 26 deselected in 1.05s at the refreshed PR head

git merge-base --is-ancestor e806affaa279f8b9d4b41bae6117a9431c99b90e origin/dev
git merge-base --is-ancestor a0020c5ac50e510467a5e80c412c7703245cf4dd origin/dev
git diff --check
PASS
```

For transparency, rerunning the original three-module command on this live
worker produced `172 passed, 31 subtests passed, 1 failed`. The sole failure was
`test_health_fails_on_stale_heartbeat`, which is outside PR #4282's changed
files. Its temporary repo had no supervisor PID, but the host's real supervisor
singleton lock was visible, so the health function reported the process alive
while correctly reporting the heartbeat stale. The focused task suites above
remain green; this ambient runtime-health coupling is deliberately not reported
as a pass.

All visible PR #4282 GitHub checks and status contexts were successful,
including Commit trailers, Runtime mirror guard, Python packaging provision,
Smoke acceptance, Forward to orchestrator, the Pantheon canonical review gate,
and the root merge gate.

## Review request

Antigravity should independently:

1. verify PR #4282 exact head and merge ancestry;
2. inspect the acceptance mapping, focused test results, and refreshed
   show/current-work/dashboard projection;
3. decide whether the documented legacy top-level source metadata is acceptable
   for this evidence-only closeout or requires a concrete reopen; and
4. bind `docs/deployment/evidence/supervisor/L12-FLEET-STATUS-SYNC-001/evidence.json`
   as `REVIEW_FILE` when approving the exact closeout-evidence PR head.

Until that independent decision is recorded against the refreshed exact PR
head, this manifest remains owner evidence, not `review_approved` evidence and
not authority to mark the task `done`.
