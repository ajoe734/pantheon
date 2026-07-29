# Execution tasks — L12 gap recovery and fleet parallelization

Generated at: `2026-07-29T07:23Z`

Live-rescue update: `2026-07-29T08:30Z`

Review correction: `2026-07-29T08:35Z`

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
- Current state: `todo`; latest observed Claude2 workers failed before the
  08:20Z runtime rescue. It remains waiting for the single `Claude2` account
  slot to free after `L12-VERIFY-KNOW-001`.
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
- Current state: `in_progress` after real supervisor/auto-worker dispatch to
  `Claude2` run `claude2-20260729T082322Z-0b3d4613`.
- Dispatch rule: do not start a duplicate while this worker is live. If it
  fails, redispatch only after preserving its log and fixing the exact blocker.
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
- Current state: `in_progress` after `Claude2` rejected the
  `65aa15358` evidence refresh at `2026-07-29T08:31:49Z`; the task was
  redispatched to `Antigravity` run
  `antigravity1-1-20260729T083305Z-e86d7488`.
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
- Required repair:
  - replace the byte-identical synthetic verifier rejected at head `65aa15358`;
  - drive real telemetry, incident, postmortem, governance/evolution,
    downstream-health, and loop-health service boundaries;
  - read persisted records back by id with distinct boundary timestamps;
  - prove duplicate rejection and restart/replay idempotency against a store;
  - remove false reviewer/governance claims such as `governed_by=Claude2`
    unless an actual governed approval exists;
  - open a fresh task PR to `dev`.

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

### `SUP-L12-TASK-BRIEF-SYNC-20260729`

- Owner: `Claude2`
- Reviewer: `Antigravity`
- Current state: new guard task to materialize after this packet merges.
- Scope:
  - `.orchestrator/task-briefs/*`
  - supervisor task-brief generation/sync code
  - worker task-brief copy/admission tests
- Acceptance:
  - reproduce the drift where status-root
    `l12_verify_{know,runtime,obs}_001.md` kept old `Codex2`/`Codex`
    owner/reviewer while dev-root had `Claude2`/`Antigravity`;
  - fix materialization so the status-root and worker worktree task brief are
    generated from the authoritative task-state row;
  - add regression covering owner, reviewer, status, last update, and `next`;
  - prove a dispatched worker receives the same canonical row in prompt and
    task-brief file;
  - do not change `.orchestrator/config.json`.

### `SUP-L12-WORKER-PYDEPS-20260729`

- Owner: `Antigravity`
- Reviewer: `Claude2`
- Current state: new guard task to materialize after this packet merges.
- Scope:
  - worker bootstrap/provisioning documentation and tests;
  - L12 verifier dependency declaration;
  - no product verifier artifact ownership.
- Acceptance:
  - reproduce the live `Claude2` KNOW worker missing `fastapi` and shared
    `/tmp/l12-alpha-pydeps` missing `uvicorn`;
  - define durable dependency provisioning for L12 verifier workers;
  - add a preflight command/test that verifies `fastapi`, `uvicorn`, `httpx`,
    `pytest`, and service-specific verifier dependencies without manual `/tmp`
    repair;
  - document the temporary 2026-07-29 runtime rescue as non-authoritative;
  - do not rely on global site-packages or mutable `/tmp` as completion proof.

### `SUP-L12-CHAIR-TRIAGE-STREAK-GUARD-20260729`

- Owner: `Claude2`
- Reviewer: `Antigravity`
- Current state: new guard task to materialize after this packet merges.
- Scope:
  - supervisor chair/failure-loop dispatch policy tests;
  - provider guardrail failure-streak handling.
- Acceptance:
  - reproduce stale `task_failure_streaks` causing
    `chair_review:reassignment_triage` to bypass cooldown and dispatch Codex2
    before L12 owner work;
  - fail closed when failure causes are stale/cleared or when the only
    reassignment target violates provider-first L12 routing;
  - add tests proving L12 owner dispatch is attempted before Codex/Codex2
    chair triage when `Claude2`/`Antigravity` lanes have eligible work;
  - record recovery evidence without editing `.orchestrator/config.json`.

### `SUP-L12-PROVIDER-FIRST-MERGE-GATE-20260729`

- Owner: `Antigravity`
- Reviewer: `Claude2`
- Current state: new guard task to materialize after this packet merges.
- Scope:
  - PR #4362 tracking and post-merge live runtime verification.
- Acceptance:
  - confirm #4362 is merged before relying on provider-first helper-claim
    behavior after supervisor restart;
  - after merge, verify live command root HEAD equals the merged runtime SHA and
    has no dirty executable/import files;
  - run the helper-claim regression test in live command root;
  - prove Codex/Codex2 cannot helper-claim L12 owner work assigned to
    Claude/Antigravity;
  - document any Human/Ops status gate as an external gate, not a fake pass.

## Dispatch order

1. Keep current KNOW on `Claude2` running; do not duplicate it.
2. Dispatch RUNTIME as soon as the real `Claude2` worker slot is available.
3. Keep LEARN blocked until a new implementation proves real service-boundary
   evidence rather than pass literals.
4. Keep OBS with `Antigravity` for implementation rework after the `Claude2`
   rejection; only request review again after a fresh PR proves service-backed
   readbacks.
5. Keep legacy `SUP-L12-FAKE-VERIFIER-GATE-20260729` and
   `SUP-L12-LIVE-WORKER-RECON-20260729` out of the active board unless they can
   run on Claude2/Antigravity, never on Codex/Codex2.
6. Materialize the four new `SUP-L12-*` guard tasks above through the
   supervisor/auto-worker path with Claude2/Antigravity-first ownership.
7. Dispatch FE only with strict live-BFF evidence and no swallowed-failure UI.
8. Dispatch HOSTED, then CLOSE.

## Current fleet health checkpoint

Observed after the 08:20Z rescue:

- `Claude2` can launch and run a governed auto worker (`L12-VERIFY-KNOW-001`).
- `Antigravity` can launch and move governed tasks, but the OBS attempt was
  correctly rejected by `Claude2` as still synthetic and is back in
  implementation repair.
- `SUP-L12-FLEET-RUNTIME-RELIABILITY-20260729` proved Antigravity owner
  dispatch by reaching `review`.
- `Claude2` account quota is effectively `1/1`, so RUNTIME waits for KNOW.
- `Antigravity` account quota is effectively `1/1`, so OBS/guard work must be
  serialized unless the account policy changes.
- Runtime deps were temporarily rescued in `/tmp/l12-alpha-pydeps`; this is
  not durable completion and must be formalized by
  `SUP-L12-WORKER-PYDEPS-20260729`.
