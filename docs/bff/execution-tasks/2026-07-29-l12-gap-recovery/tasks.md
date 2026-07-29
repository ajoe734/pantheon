# Execution tasks — L12 gap recovery and fleet parallelization

Generated at: `2026-07-29T07:23Z`

Source audit:
`docs/04/pantheon_twelve_loop_gap_2026-07-26/archive/THREE_PASS_GAP_AUDIT_2026-07-29T0710Z.md`

This task plan avoids duplicate artifact ownership. Existing L12 task rows
remain canonical for product implementation. New `SUP-*` tasks are guard and
coordination lanes only.

## Active product lanes

### `L12-FE-TRUTH-001`

- Owner: `Antigravity`
- Reviewer: `Claude2`
- Current state: `blocked`; no confirmed live worker at the latest process
  check.
- Scope: `execute-plans` UI, tests, and task evidence.
- Required delivery:
  - committed hosted 1440px and 390px evidence;
  - axe, keyboard, reduced-motion proof;
  - strict live-BFF network capture;
  - hosted FE and BFF identity manifest;
  - all twelve canonical loop ids rendered;
  - seed/stale/degraded/failed records cannot render green;
  - live-call failure renders error/unknown, not a false empty state;
  - BFF envelope provenance surfaced in the UI.

### `L12-VERIFY-RUNTIME-001`

- Owner: `Claude2`
- Reviewer: `Antigravity`
- Current state: `todo`; latest observed Claude2 worker
  `claude2-20260729T071353Z-4c29a045` failed at `2026-07-29T07:19:59Z`, and
  the supervisor preempted the row back to `todo`.
- Scope: `scripts/verify_twelve_loop_runtime.py` and
  `docs/deployment/evidence/twelve-loop-gap/L12-VERIFY-RUNTIME-001`.
- Required delivery:
  - immutable approval to `DeploymentPlan`;
  - one `RuntimeBinding` and one governed-paper worker;
  - no duplicate binding/order on duplicate or crash-after-side-effect paths;
  - kill, pause, retire, and full restart convergence;
  - missing/mismatched signal scope rejected and durable-DLQ'd;
  - order/fill/position/heartbeat/BFF truth share authoritative correlation.

### `L12-VERIFY-KNOW-001`

- Owner: `Claude2`
- Reviewer: `Antigravity`
- Current state: `todo`.
- Dispatch rule: start as soon as Claude2 has a true free worker slot.
- Required delivery:
  - real Persona requirement to durable `SourceRecord`;
  - one mutable `StrategySpec` draft;
  - approved `StrategySpec` to authoritative `ExperimentRun`;
  - unapproved/immutable negative gates;
  - duplicate/concurrency/provider/registry/research failure and restart cases;
  - BFF/controller terminal truth readbacks.

### `L12-VERIFY-LEARN-001`

- Owner: `Antigravity`
- Reviewer: `Claude2`
- Current state: `blocked` by rejected fake PRs.
- Rejected heads:
  - #4354 `9115d22e07851105217c4529927929c6a806cf7e`
  - #4356 `f11f65124ce7337ccbdb1c1f87e7a5680e0b3499`
  - #4358 `2d35275144edcfcf803da78735008cac5ab92f77`
- Required delivery:
  - no self-generated pass literals;
  - training/session eval and persona commit before/after readback;
  - Agora `DatasetVersion` and acknowledged handoff persisted ids;
  - imitation `ShadowImitationCandidate` from real dataset without seed fallback;
  - consultation memo and governance handoff persisted ids;
  - tenant/RBAC, duplicate, restart, DLQ, and no-runtime-mutation negative cases;
  - evidence manifest with concrete ids and checksums.

### `L12-VERIFY-OBS-001`

- Owner: `Antigravity`
- Reviewer: `Claude2`
- Current state: `blocked` after rejected synthetic PRs.
- Rejected heads:
  - #4355 `8ba6792a2f00010ad1c401fd4cf526bde65269b8`
  - #4360 `f10a6015a48947adbd51a2b2fc12a8a78f426d1e`
- Required delivery:
  - real telemetry and drift readbacks;
  - correlated incident from heartbeat loss/order rejection/drawdown;
  - postmortem and governed `EvolutionDecision`;
  - approved action terminal downstream receipt with retry/compensation;
  - BFF downstream stop and recovery telemetry;
  - no locally fabricated UUID proof.

## Dependent product lanes

### `L12-HOSTED-001`

- Owner: `Antigravity`
- Reviewer: `Claude2`
- Current state: `todo`.
- Start only after FE, KNOW, LEARN, RUNTIME, and OBS are reviewed and merged.
- Required delivery:
  - exact Pantheon and execute-plans commits/images in hosted manifest;
  - all required workers healthy;
  - twelve accepted current controller records;
  - full-stack restart without duplicate effects;
  - source-to-health and runtime-to-incident-to-evolution drills;
  - hosted desktop/mobile/auth/tenant/no-live-capital evidence.

### `L12-CLOSE-001`

- Owner: `Claude2`
- Reviewer: `Antigravity`
- Human/Ops signoff: required.
- Current state: `todo`.
- Start only after hosted proof and all predecessor archives.
- Required delivery:
  - every predecessor `done` with merged reviewed current evidence;
  - every evidence manifest schema/checksum/replay passes;
  - current controller and terminal actual-state readbacks accepted;
  - no residual stale deployment, security, or maturity contradiction;
  - formal Human/Ops verdict archived.

## New non-overlapping supervisor guard tasks

These are guard and coordination lanes, not product implementation lanes.
Dispatch constraint: they must run through real Claude2/Antigravity supervisor
auto-workers. If the supervisor helper-claims them to Codex/Codex2, that
execution is invalid, must be terminated, and must not be counted as fleet
progress.

### `SUP-L12-FAKE-VERIFIER-GATE-20260729`

- Owner: `Claude2`
- Reviewer: `Antigravity`
- Current state: archived plan only; active task-board row removed after
  repeated invalid Codex/Codex2 helper-claims. Re-create only when real
  Claude2/Antigravity capacity exists.
- Artifacts:
  - `docs/deployment/evidence/twelve-loop-gap/SUP-L12-FAKE-VERIFIER-GATE-20260729`
- Acceptance:
  - monitor open L12 verifier PR heads for self-attesting pass-printers;
  - fail closed if verifier constructs `status: pass` without service calls;
  - fail closed if evidence uses synthetic UUIDs as proof;
  - comment/close invalid PRs and reopen canonical task rows with exact head ids;
  - record every action in a task-scoped evidence file.

### `SUP-L12-LIVE-WORKER-RECON-20260729`

- Owner: `Antigravity`
- Reviewer: `Claude2`
- Current state: archived plan only; active task-board row removed after
  repeated invalid Codex/Codex2 helper-claims. Re-create only when real
  Antigravity/Claude2 capacity exists.
- Artifacts:
  - `docs/deployment/evidence/twelve-loop-gap/SUP-L12-LIVE-WORKER-RECON-20260729`
- Acceptance:
  - compare authoritative task rows against live `worker_runner` processes;
  - list fake `in_progress`, stale `review`, and missing live-worker rows;
  - confirm supervisor uses `/home/lupin/pantheon-ci-deploy/dev-root`;
  - confirm active Claude2 and Antigravity lanes by run id;
  - do not edit `.orchestrator/config.json`;
  - produce a dispatch-health evidence record.

## Dispatch order

1. Keep RUNTIME `todo` until a real Claude2 worker slot is available.
2. Keep LEARN blocked until a new implementation proves real service-boundary
   evidence rather than pass literals.
3. Keep OBS blocked until a new implementation proves durable telemetry,
   incident, evolution, downstream receipt, and BFF health readbacks.
4. Keep both `SUP-*` guard tasks out of the active board unless they can run
   on Claude2/Antigravity, never on Codex/Codex2.
5. Dispatch KNOW when a Claude2 worker slot is free.
6. Dispatch FE only with strict live-BFF evidence and no swallowed-failure UI.
7. Dispatch HOSTED, then CLOSE.
