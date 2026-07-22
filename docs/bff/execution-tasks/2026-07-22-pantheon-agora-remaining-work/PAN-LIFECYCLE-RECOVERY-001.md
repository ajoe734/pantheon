# PAN-LIFECYCLE-RECOVERY-001 — Recover lifecycle projection and make freshness observable

Priority: P0
Repository: `ajoe734/pantheon`
Merge target: `dev`
Owner: Codex2
Reviewer: Codex
Depends on: `OPS-DISPATCH-LEASE-SYNC-001`

## Objective

Recover the stopped lifecycle/loop-run projector after its `ENOSPC` failure,
prevent unbounded generation debris from recurring, and make projector health
and freshness part of the operator-facing readiness truth.

## Current evidence

- Container: `pantheon-loop-run-projector-scheduler-1`.
- Failure: `OSError: [Errno 28] No space left on device` while publishing
  lifecycle generation 5036.
- Restart count observed: 81.
- `current` last advanced at 2026-07-21 11:58 UTC.
- Root filesystem has recovered to 23% use, but the scheduler remains
  exited/unhealthy between restart attempts.
- `/readyz` currently checks runtime-manager, governance, and deployment only.

## Owned scope

- `services/trade_journey/lifecycle_projector.py` and focused tests
- projector compose health/restart configuration
- BFF readiness dependency/freshness projection
- deployment/runbook and task-scoped dev evidence

## Required work

1. Perform the smallest safe live rescue first if needed. Preserve the last
   accepted generation and inspect incomplete `.tmp` generations before any
   cleanup.
2. Add bounded generation retention and safe cleanup for abandoned staging
   generations. Never delete `current` or its referenced generation.
3. Ensure restart after transient disk exhaustion converges instead of
   generating an infinite crash loop.
4. Expose worker status, current generation, source high watermark, last
   successful publish time, and stale/error reason through readiness.
5. Add disk/freshness thresholds and regression coverage for ENOSPC during
   publish and error-record publication.
6. Deliver the repair through PR and redeploy it; do not leave a live-only
   restart as the final state.

## Acceptance

- Scheduler is continuously healthy through at least three projection cycles.
- A newly created lifecycle event advances `current` and is readable through
  the Trade Journey/loop-run projection.
- Restarting the scheduler preserves the accepted generation and resumes.
- `/readyz` or an explicitly linked dependency endpoint fails closed when the
  projector is stopped or stale and becomes healthy after recovery.
- Retention has a deterministic bound and tests prove it never removes the
  active generation.
- Live rescue, PR, merge SHA, deployed SHA, disk/freshness readback, and
  residual risk are archived.

## Exclusions

- No production data deletion.
- No hand-editing lifecycle payloads or advancing the `current` symlink to
  fabricate freshness.
- No live-capital or order-routing activity.
