# BFF-CONSOL Final Acceptance Packet

Task: `BFF-CONSOL-027`  
Owner: `Copilot`  
Reviewer: `Claude`  
Date: 2026-05-15

## Summary

BFF Consolidation tasks `BFF-CONSOL-001` through `BFF-CONSOL-026` have enough
evidence to close the final acceptance packet. The final blocker,
`BFF-CONSOL-023`, is complete via runtime strict main cutover evidence:
authenticated read smoke passed, hosted browser BFF/SSE probe passed, and strict
fallback regression checks passed with writes disabled.

The fixed elapsed-day soak gate has been removed from the closeout path. Strict
cutover is accepted on regression evidence, not a calendar wait.

## Task Evidence Map

| Task | Status | Evidence / commit |
|---|---|---|
| `BFF-CONSOL-001` | done | `ai-task-archive/tasks/BFF-CONSOL-001.json` |
| `BFF-CONSOL-002` | done | `ai-task-archive/tasks/BFF-CONSOL-002.json` |
| `BFF-CONSOL-003` | done | `ai-task-archive/tasks/BFF-CONSOL-003.json` |
| `BFF-CONSOL-004` | done | `support/evidence/BFF-CONSOL-004-closeout.md` |
| `BFF-CONSOL-005` | done | `support/evidence/BFF-CONSOL-005/review-claude-2026-05-13.md` |
| `BFF-CONSOL-006` | done | `ai-task-archive/tasks/BFF-CONSOL-006.json` |
| `BFF-CONSOL-007` | done | `support/evidence/BFF-CONSOL-007/review-claude-2026-05-13.md` |
| `BFF-CONSOL-008` | done | `ai-task-archive/tasks/BFF-CONSOL-008.json` |
| `BFF-CONSOL-009` | done | `ai-task-archive/tasks/BFF-CONSOL-009.json` |
| `BFF-CONSOL-010` | done | `ai-task-archive/tasks/BFF-CONSOL-010.json` |
| `BFF-CONSOL-011` | done | `support/evidence/BFF-CONSOL-011-sse-replay-smoke.json` |
| `BFF-CONSOL-012` | done | `support/evidence/BFF-CONSOL-012-sse-backpressure.json` |
| `BFF-CONSOL-013` | done | `ai-task-archive/tasks/BFF-CONSOL-013.json` |
| `BFF-CONSOL-014` | done | `ai-task-archive/tasks/BFF-CONSOL-014.json` |
| `BFF-CONSOL-015` | done | `ai-task-archive/tasks/BFF-CONSOL-015.json` |
| `BFF-CONSOL-016` | done | `support/evidence/BFF-CONSOL-016-detail-smoke-a.json`; commits `6b59cbd2`, `72a65d78` |
| `BFF-CONSOL-017` | done | `support/evidence/BFF-CONSOL-017-detail-smoke-b.json`; commits `aea5d8b4`, `83c42310` |
| `BFF-CONSOL-018` | done | `support/evidence/BFF-CONSOL-018-detail-smoke-c.json` |
| `BFF-CONSOL-019` | done | commit `34fa7aec`; `support/sidecars/BFF-CONSOL-019/BFF-CONSOL-019-SIDECAR-BFF-HANDOFF.md` |
| `BFF-CONSOL-020` | done | `support/evidence/BFF-CONSOL-020-closeout.md`; execute-plans commit `30b4ed3` |
| `BFF-CONSOL-021` | done | `support/evidence/BFF-CONSOL-021-dual-write-soak.json` |
| `BFF-CONSOL-022` | done | `support/evidence/BFF-CONSOL-022-staging-strict-soak.md`; browser/read evidence under `support/evidence/BFF-CONSOL-022-*` |
| `BFF-CONSOL-023` | done | `support/evidence/BFF-CONSOL-023-prod-strict-soak.md`; browser/read evidence under `support/evidence/BFF-CONSOL-023-*` |
| `BFF-CONSOL-024` | done | `ai-task-archive/tasks/BFF-CONSOL-024.json` |
| `BFF-CONSOL-025` | done | execute-plans commit `226d7e4`; `support/sidecars/BFF-CONSOL-025/BFF-CONSOL-025-REVIEW.md` |
| `BFF-CONSOL-026` | done | `support/evidence/BFF-CONSOL-026-closeout.md`; commit `41d6fe4d` |

Archive note: `ai-status show` does not resolve snapshots for
`016/017/019/020/025/026`, but the evidence files and commits above establish
their terminal state. This is an archive-materialization gap, not an execution
blocker.

## Contract Diff Baseline

Covered by:

- `BFF-CONSOL-001` backend route manifest extractor.
- `BFF-CONSOL-002` frontend route manifest extractor.
- `BFF-CONSOL-003` CI route diff baseline.
- `scripts/bff_route_manifest_backend.py`.
- execute-plans route manifest artifacts referenced by the archived tasks.

Acceptance: route manifest comparison exists and no longer blocks final
consolidation.

## Live Smoke

Read smoke:

- `support/evidence/BFF-CONSOL-022-day1-authenticated-live.json`
- `support/evidence/BFF-CONSOL-023-authenticated-live.json`

Latest result recorded for both strict cutover surfaces:

- total probes: `32`
- passed: `32`
- failed: `0`
- read probes: `30`
- write probes: `0`
- live capital side effects: `false`

Write and command smoke:

- `support/evidence/BFF-CONSOL-020-closeout.md`
- `support/evidence/BFF-CONSOL-021-dual-write-soak.json`

Acceptance: writes remain gated; receipt dual-write and replay/conflict paths
have closeout evidence.

## SSE Evidence

Covered by:

- `support/evidence/BFF-CONSOL-011-sse-replay-smoke.json`
- `support/evidence/BFF-CONSOL-012-sse-backpressure.json`
- `support/evidence/BFF-CONSOL-022-day1-browser/hosted-browser-bff-probe-2026-05-15.md`
- `support/evidence/BFF-CONSOL-023-main-browser/hosted-browser-bff-probe-2026-05-15.md`

Hosted probes observed `/bff/events/stream` with HTTP `200` and no failed BFF
requests.

## Command Receipt Sample

Covered by:

- `BFF-CONSOL-019` command admission adapter commit `34fa7aec`.
- `BFF-CONSOL-020` final command action client evidence.
- `BFF-CONSOL-021` dual-write soak evidence.

Acceptance: command receipt, replay, conflict, confirm-token, and approval
precondition paths are represented by closeout artifacts.

## Cutover Log

Dev strict cutover:

- `BFF-CONSOL-022` completed against the reachable public dev deployment using
  runtime strict override.
- `REAL_WRITES=false`.
- Authenticated read smoke and hosted browser BFF/SSE probe passed.

Main/dev front-end strict cutover:

- `BFF-CONSOL-023` completed via runtime strict cutover.
- Hosted bundle contains `__PANTHEON_BFF_RUNTIME__` and
  `pantheon.integration.fallback`.
- Authenticated read smoke passed `32/32`.
- Hosted browser probe passed and observed `/bff/me`, `/bff/v5/control-room`,
  and `/bff/events/stream`.
- F15 strict regression and focused F01 strict/no-fallback checks passed.

Non-blocking follow-up:

- Lovable has not yet emitted a new build-time asset containing
  `VITE_BFF_FALLBACK:"strict"`. This is recorded in
  `support/evidence/BFF-CONSOL-023-prod-strict-soak.md` as an ops follow-up,
  because runtime strict evidence satisfies the current cutover gate.

## Regression Follow-up State

- Fixed elapsed-day soak gates are removed.
- `BFF-CONSOL-028` exists as follow-up for deferred seed adjunct helpers.
- Build-time Lovable env publish remains a non-blocking ops follow-up.
- `FE-INT-GATE-OIDC-DEV-LOGIN` is archived done; BFF strict smoke currently uses
  the dev bearer smoke path documented in the evidence.

## Seed Post-state

Covered by:

- `BFF-CONSOL-007` seed taxonomy.
- `BFF-CONSOL-015` mock-only badge/live mode handling.
- `BFF-CONSOL-025` seed-only live surface elimination.
- `BFF-CONSOL-026` fail-hard closeout.
- `BFF-CONSOL-028` deferred adjunct follow-up.

Acceptance: seed fallback is explicit, visible, or disabled in strict live
paths; remaining adjunct surfaces are tracked outside the 001..026 closure.

## Final Decision

`BFF-CONSOL-027` is ready for Claude review. The final BFF consolidation packet
contains the required contract diff baseline, live smoke, SSE evidence, command
receipt sample, cutover log, regression follow-up status, and seed post-state.
