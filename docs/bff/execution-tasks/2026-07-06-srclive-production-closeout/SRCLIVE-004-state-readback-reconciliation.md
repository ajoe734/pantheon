# SRCLIVE-004 - State And Readback Reconciliation

Status: implementation complete; reconciliation/fresh verification remains.

Recommended owner: Codex

Recommended reviewer: Copilot or Codex2

Do not assign to Claude or Claude2 while their quota is exhausted.

## Goal

Close SRCLIVE-004 without reimplementing completed work. The task is to restore accurate status/archive evidence and, where possible, refresh live readback proof.

## Evidence Already Published

- PR #2539: SRCLIVE-004: repair readback verifier and public source fetch, merge 87c382c779869c8920a73aa794f308c9acb8046c.
- PR #2548: SRCLIVE-004: accept source ingest job parameters, merge 80ae5544591dad98d2fb1a25fe45fcb9f5abbb26.
- PR #2554: SRCLIVE-004: tolerate source-only Stooq readback, merge f353139ed446d97946a7745a3aaf0a5ca8a634b6.
- PR #2557: SRCLIVE-004: record closeout evidence, merge 4ecd5f78652fe82f0e07a4129bff9736dc4b443f.

## Current Gap

The current status root returns Unknown task for SRCLIVE-004, and no current archive snapshot was found in the active root audit. That is a state/archive gap, not an implementation gap.

## Required Execution

1. Re-run the SRCLIVE readback verifier against the intended dev BFF if the runtime is reachable.
2. If the verifier cannot run, record the exact blocker and the command used.
3. Reconcile task/archive records so SRCLIVE-004 is discoverable as completed with links to the merged PRs above.
4. Do not modify source-ingest or BFF behavior unless the fresh verifier proves a real regression.

## Acceptance Criteria

1. SRCLIVE-004 is represented in the archive/status record or a reviewed closeout document explains why the active status root cannot be safely mutated.
2. Fresh readback proof is attached, or a concrete runtime blocker is recorded.
3. No duplicate implementation task is created.
4. PR checks pass and the closeout PR is merged.

## 2026-07-07 Read-Only Verifier Attempt

Command run from the clean task worktree:

python3 scripts/verify_srclive_readback.py --json

Result: blocked by live BFF auth, not by a verifier code error.

The dev BFF returned HTTP 401 AUTH_REQUIRED for /bff/v5/execution/persona-health with SESSION_LOGGED_OUT. A worker with a valid operator/admin token must rerun the same command with BFF_TOKEN set, and may add SOURCE_INGEST_BASE only when the source-ingest service is reachable from the execution environment.

This confirms the remaining SRCLIVE-004 work is fresh readback plus state/archive reconciliation unless the authenticated verifier later proves a real regression.
