# OPS-DISPATCH-LEASE-SYNC-001 — Restore governed dispatch status sync

Priority: P0
Repository: `ajoe734/pantheon`
Merge target: `dev`
Owner: Codex
Reviewer: Codex2

## Objective

Make a dispatched worker use its supervisor-issued run lease when invoking the
governed status command, so one execution attempt can progress from `todo` to a
terminal state without a missing-lease exit loop.

## Owned scope

- `.orchestrator/supervisor.py`
- focused supervisor tests for dispatched status sync
- one task-scoped review/evidence record

## Required work

1. Compare PRs #3936 and #3948 with current `origin/dev`.
2. Choose one canonical repair: rebase/update the better PR, or close both and
   create one replacement. Do not merge both and do not create a third
   implementation without superseding the duplicates.
3. Pass the started worker run ID as `ORCH_RUN_ID` to `scripts/ai_status.py`
   while preserving the installed command-runtime/status-root bindings.
4. Add coverage for a valid lease, missing/expired lease rejection, reviewer
   dispatch, and no cross-task/cross-root authority.
5. Merge to `dev`, deploy the command runtime, and run a lifecycle smoke that
   reaches `todo -> in_progress -> review -> review_approved -> done`.

## Acceptance

- Exactly one repair PR is merged and duplicate PRs are closed with a
  supersession reference.
- Focused supervisor/status-command tests pass.
- Live command-runtime SHA contains the repair.
- One harmless task completes through the governed lifecycle without a
  missing-lease or generic-exit loop.
- No direct canonical-state write or lease bypass is introduced.

## Exclusions

- Do not loosen `validate_active_status_command_lease`.
- Do not assign or complete unrelated product tasks as test fixtures.
- Do not change provider credentials or worker capacity.
