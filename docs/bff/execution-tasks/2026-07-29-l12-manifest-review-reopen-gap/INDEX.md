# L12 Manifest Review Reopen Gap Execution Packet

Packet ID: `2026-07-29-l12-manifest-review-reopen-gap`

Source audit:
`docs/04/pantheon_twelve_loop_gap_2026-07-26/archive/L12_MANIFEST_REVIEW_REOPEN_GAP_2026-07-29T0225Z.md`

Machine-readable task split:
`docs/bff/execution-tasks/2026-07-29-l12-manifest-review-reopen-gap/tasks.json`

Generated at: `2026-07-29T02:40:00Z`

## Goal

Repair the exact #4329 reviewer rejection without touching
`.orchestrator/config.json` and without substituting Codex chat subagents for
real supervisor/auto-worker fleet work.

## Current Immediate Truth

- `L12-MANIFEST-001` is `in_progress`.
- PR #4329 is open at head
  `114bfce34dbe1d6abf48c9d7759bd2e7bff5aa87`.
- Branch checks and root-freeze are green on that head.
- `Pantheon canonical review gate` is failure on that head.
- The failure is substantive: `20/27` healthchecks, seven missing
  health/heartbeat surfaces, missing auth/volume matrix, missing daemon
  restart proof, and stale PR/readback wording.

## Dispatch Model

These are workstreams under `L12-MANIFEST-001`, not independent canonical
owners of `docker-compose.yml`. The final owner must integrate the accepted
patches/evidence into #4329 or a replacement PR.

## Workstreams

Run these now where real lanes are available:

1. `L12-MANIFEST-HC-ALPHA-SRC-20260729`
2. `L12-MANIFEST-HC-IMIT-CAP-20260729`
3. `L12-MANIFEST-HC-REC-20260729`
4. `L12-MANIFEST-AUTH-VOLUME-MATRIX-20260729`
5. `L12-MANIFEST-RESTART-PROOF-20260729`
6. `SUP-L12-FLEET-RUNTIME-RELIABILITY-20260729`

Run this only after the first five manifest streams have concrete outputs:

7. `L12-MANIFEST-CLOSEOUT-ALIGN-20260729`

## Do Not Claim Done Until

- all seven missing worker health/heartbeat gaps are closed or explicitly
  waived;
- auth and durable-volume applicability are proven per worker;
- restart proof or waiver is sound;
- #4329 or replacement PR has refreshed evidence/readback/checksum;
- exact-head canonical review gate is success;
- the PR is merged and `L12-MANIFEST-001` is archived.
