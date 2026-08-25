# Auto Integrator Operations Guide

Status: active operations guide for `scripts/git/auto_integrator.py`.
Last updated: 2026-08-25 (`SUP-AUTO-INTEGRATOR-CANONICAL-CLOSEOUT-20260825`)

`scripts/git/auto_integrator.py` is a serialized integration runner that bridges
the gap between a task reaching `review_approved` and its PR landing in `dev`.

```text
canonical review_approved task -> clean task PR into dev -> local rebase smoke -> exact-head merge
-> leave in review_approved for supervisor owned_finalize dispatch
```

## Core Lifecycle & Authority Boundaries

1. **Canonical Truth Binding**:
   - Auto-integrator discovers candidates from the canonical status root
     (`PANTHEON_STATUS_ROOT` environment variable or `--status-file` option).
   - Local task worktree copies (`ROOT / ai-status.json`) are disposable execution projections
     and do not mask canonical runtime truth.

2. **Review Gate Enforced**:
   - Merge authority is governed by `scripts/git/task_review_merge_gate.py`.
   - For tasks requiring independent review, only the exact head approved by the assigned
     reviewer can be merged (`--match-head-commit`).
   - Standing auto-merge requests on gated PRs are actively revoked before evaluation.

3. **Owner-Finalize Handoff (No Unauthorized `done` Mutation)**:
   - The auto-integrator merges the PR into `dev` and leaves the task in `review_approved`.
   - The integrator **never** calls `scripts/ai_status.py done` or changes task status to `done`.
   - The supervisor detects that the task's PR is merged into `dev` and dispatches the task owner
     via `owned_finalize_dispatch` with an owner lease.
   - The owner then performs canonical closeout (review manifest binding, verification,
     evidence checks, delivery metadata, and `scripts/ai-status.sh done`).

4. **Idempotent Reruns on Merged PRs**:
   - If the task PR is already merged into `dev`, reruns report `already_merged` and leave the
     task in `review_approved` for owner finalization without creating spurious unblock tasks.

## Safety Model

- **Dry-run by default**: `--execute` is required to mutate git/GitHub state.
- **Single-flight lock**: `.orchestrator/auto-integrator.lock` under `PANTHEON_STATUS_ROOT` (or repo root)
  ensures only one integrator process executes at a time.
- **Fail-closed checks**: Draft PRs, missing PRs, CI failures, rebase conflicts, or head drift
  produce an `INTEGRATION-UNBLOCK-*` task to assign the blocker back to the owner/reviewer.

## CLI Usage

### Dry-run discovery and gate validation
```bash
python3 scripts/git/auto_integrator.py --json
```

### Execute one integration pass
```bash
PANTHEON_STATUS_ROOT=/home/lupin/pantheon-ci-deploy/coordination-root \
  python3 scripts/git/auto_integrator.py --execute --max-tasks 1
```

### Test against an isolated status file
```bash
python3 scripts/git/auto_integrator.py --status-file /tmp/test-status.json --json --no-lock
```

## Scheduled Runner

`scripts/run-auto-integrator.sh` is the cron wrapper that passes canonical status/config paths:

```bash
PANTHEON_STATUS_ROOT=/home/lupin/pantheon \
  bash scripts/run-auto-integrator.sh
```

Installed via `scripts/auto_integrator_install.py` to run every 5 minutes tagged `# pantheon-auto-integrator`.
