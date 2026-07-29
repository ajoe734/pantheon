# Execution tasks — L12 gap recovery and fleet parallelization

Generated at: `2026-07-29T07:23Z`

Live-rescue update: `2026-07-29T08:30Z`

Review correction: `2026-07-29T08:35Z`

Fleet correction: `2026-07-29T08:40Z`

Dispatch-priority correction: `2026-07-29T08:48Z`

Terminal-fallback correction: `2026-07-29T09:08Z`

Clean command-root correction: `2026-07-29T09:31Z`

Current-state refresh: `2026-07-29T10:25Z`

Runtime/fleet delta: `2026-07-29T11:40Z`

Source audit:
`docs/04/pantheon_twelve_loop_gap_2026-07-26/archive/THREE_PASS_GAP_AUDIT_2026-07-29T1140Z.md`

This task plan avoids duplicate artifact ownership. Existing L12 task rows
remain canonical for product implementation. New `SUP-*` tasks are guard and
coordination lanes only.

## 2026-07-29T10:25Z dispatch delta

The current dispatch authority is
`docs/04/pantheon_twelve_loop_gap_2026-07-26/archive/THREE_PASS_GAP_AUDIT_2026-07-29T1025Z.md`.

- `SUP-L12-REVIEW-PRIORITY-GATE-20260729` is complete and archived after
  #4365/#4366. Do not redispatch it.
- Live supervisor root is
  `8ea01a8e3993b3dabc6cd475c7058d299eaf4a01` and watchdog reports
  `supervisor_healthy`.
- #4367 is stale duplicate closeout receipt work and must be retired or
  superseded, not counted as product evidence.
- #4364 (`L12-VERIFY-OBS-001`) is open but behind and still needs exact
  Claude2 review after refresh/rebase.
- #4330 is merged but `L12-MANIFEST-REVIEW-GAP-TASKS-20260729` remains
  canonical-row blocked because closeout metadata must be reconciled.
- Active product work remains KNOW, LEARN, RUNTIME, OBS, FE, HOSTED, CLOSE.

## 2026-07-29T11:40Z runtime/fleet delta

The current dispatch authority is now
`docs/04/pantheon_twelve_loop_gap_2026-07-26/archive/THREE_PASS_GAP_AUDIT_2026-07-29T1140Z.md`.

- #4371 is merged to `dev` and live-promoted as
  `c1e396495d37a1c9dfeea5704e7eb73db6acde0e`; the deployed supervisor root
  has no config diff from
  `/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json`.
- Live supervisor restart was intentional for deployment only; new PID was
  `4191254`.
- Human/Ops performed a temporary live repair by clearing stale Claude2/L12
  `missing_process` failure streaks from runtime state. Backup:
  `.orchestrator/state.json.bak-human-ops-clear-l12-stale-failure-streaks-20260729T1133Z`.
- That live repair enabled real Claude2 fleet dispatch:
  `claude2-20260729T113336Z-08eddb2f` on `L12-VERIFY-OBS-001`.
- Antigravity completed real supervisor work:
  `antigravity1-1-20260729T112638Z-2b127a26` on
  `OPS-PROMOTE-PR-CI-TRIGGER-001`.
- Helper-claim routing still fell to Codex2 while Claude2 was busy:
  `SUP-L12-STALE-PR-RETIRE-20260729` and
  `SUP-L12-FLEET-DISPATCH-READBACK-20260729` both launched Codex2 fallback
  workers and failed with SIGTERM/code 143. Those runs are invalid as
  provider-first proof.
- The failed Codex2 fallback runs returned the SUP-L12 rows to
  Antigravity/Claude2 ownership, but this exposed missing tests for
  running-worker/row-owner reconciliation and long finalize/lease pressure.

New guard tasks from this delta:

1. `SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729`
2. `SUP-L12-HELPER-CLAIM-BUSY-PREFERRED-LANE-20260729`
3. `SUP-L12-RUNNING-OWNER-RECONCILE-20260729`
4. `SUP-L12-LONG-FINALIZE-LEASE-20260729`

## Current parallel dispatch waves

### Wave 0 — supervisor/fleet hygiene and closeout repair

#### `SUP-L12-STALE-PR-RETIRE-20260729`

- Owner: `Antigravity`
- Reviewer: `Claude2`
- Scope:
  - GitHub PR #4367
  - GitHub PR #4364 status/readiness table only; do not close if still valid
  - GitHub PR #4313 / #4297 stale closeout state
  - task row/source-ref truth for stale L12 PRs
- Acceptance:
  - #4367 is confirmed superseded by archived #4365/#4366 evidence or a
    concrete reason is recorded if it is not;
  - stale duplicate PRs are closed or superseded with exact PR/head rationale;
  - active product PR #4364 is left open unless exact review proves it is stale
    or invalid;
  - evidence table records PR number, state, head SHA, owner task, and action.

#### `SUP-L12-MERGED-ROW-RECONCILE-20260729`

- Owner: `Claude2`
- Reviewer: `Antigravity`
- Scope:
  - `L12-MANIFEST-REVIEW-GAP-TASKS-20260729`
  - PR #4330 / merge `d9cbbbfa2b0d4076f939a6d0fcc921406993d7af`
  - other merged-but-nonterminal L12 rows found by exact evidence
- Acceptance:
  - locate or create task-brief-shaped merged evidence;
  - use governed `reconcile_merged_done` only after evidence file and delivery
    commit are already merged to `dev`;
  - otherwise open a minimal closeout-evidence PR, merge it, then archive;
  - no implementation restart for already merged work.

#### `SUP-L12-FLEET-DISPATCH-READBACK-20260729`

- Owner: `Antigravity`
- Reviewer: `Claude2`
- Scope:
  - live supervisor health
  - worker-runtime heartbeats/status
  - first post-#4365 L12 review/finalize dispatch
- Acceptance:
  - live root SHA is `8ea01a8e3993b3dabc6cd475c7058d299eaf4a01` or newer;
  - actual `worker_runner.py` PIDs and run ids are captured;
  - next eligible L12 review/finalize dispatch goes to Claude2 or Antigravity;
  - Codex/Codex2 dispatch is recorded only as fallback/runtime repair, not as
    preferred fleet proof;
  - `.orchestrator/config.json` is not edited.

### Wave 1 — product verifier work, maximally parallel

Run these in parallel where provider slots allow. Owners/reviewers below are
the preferred lanes and should not be reassigned to Codex-family lanes merely
because they are available.

1. `L12-VERIFY-KNOW-001`: Claude2 owner, Antigravity reviewer.
2. `L12-VERIFY-LEARN-001`: Antigravity owner, Claude2 reviewer.
3. `L12-VERIFY-RUNTIME-001`: Claude2 owner, Antigravity reviewer.
4. `L12-VERIFY-OBS-001`: Antigravity owner, Claude2 reviewer.
5. `L12-FE-TRUTH-001`: Antigravity owner, Claude2 reviewer.

Each Wave 1 lane must prove service-boundary calls, persisted ids, before/after
readbacks, negative tests, restart/replay behavior, exact-head review, merge,
and archive. A pass-printer, generated UUID, local-only fixture, or narrow CI
does not satisfy acceptance.

### Wave 2 — hosted and final closeout

Start only after Wave 1 product lanes are accepted, merged, and archived.

1. `L12-HOSTED-001`: Antigravity owner, Claude2 reviewer.
2. `L12-CLOSE-001`: Claude2 owner, Antigravity reviewer, Human/Ops signoff.

### Wave 3 — regression guardrails

These can run in parallel with Wave 1 if artifact scopes do not overlap:

1. `SUP-PROVIDER-FIRST-HELPER-GUARD-20260729` / PR #4362.
2. `SUP-L12-FLEET-RUNTIME-RELIABILITY-20260729` / PR #4363.
3. `SUP-L12-TASK-BRIEF-SYNC-20260729`.
4. `SUP-L12-WORKER-PYDEPS-20260729`.
5. `SUP-L12-CHAIR-TRIAGE-STREAK-GUARD-20260729`.
6. `SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729`.
7. `SUP-L12-HELPER-CLAIM-BUSY-PREFERRED-LANE-20260729`.
8. `SUP-L12-RUNNING-OWNER-RECONCILE-20260729`.
9. `SUP-L12-LONG-FINALIZE-LEASE-20260729`.

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
- Current state: `todo`; the `Claude2` owner run
  `claude2-20260729T083911Z-b350d04f` was superseded at
  `2026-07-29T08:43:00Z` to free `Claude2` for higher-priority
  review/finalize work. No terminal RUNTIME evidence or PR exists yet.
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
- Current state: `todo` after the latest `Claude2` owner run
  `claude2-20260729T083251Z-33cfa39f` failed and the supervisor preempted the
  row to free `Claude2` for higher-priority review/runtime work.
- Dispatch rule: do not count the failed/preempted run as delivery. Redispatch
  only after preserving the log and ensuring worker bootstrap dependencies and
  task-brief context are stable.
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
- Current state: `review`; after two `Claude2` rejections, Antigravity opened
  PR #4364 at head `ffd90cab757ee3939cbf7c4e5e5c7956f29f0bdd` with checks
  green but merge state `BLOCKED`. `Claude2` review has not run yet because the
  supervisor repeatedly dispatched non-L12 `OPS-PROMOTE-PR-CI-TRIGGER-001`
  review to the only Claude2 slot.
- Prior failed attempts: the
  `65aa15358` evidence refresh was rejected at `2026-07-29T08:31:49Z`; the
  follow-up anchor `ac87dc46a` was rejected at `2026-07-29T08:38:37Z` because
  it still used the same self-attesting verifier and only regenerated UUIDs and
  timestamps.
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
  - replace the byte-identical synthetic verifier rejected at heads
    `65aa15358` and `ac87dc46a`;
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

### `SUP-L12-REVIEW-PRIORITY-GATE-20260729`

- Owner: `Claude2`
- Reviewer: `Antigravity`
- Current state: new guard task to materialize after this packet merges.
- Latest live observation: the task was materialized and churned through
  `codex-20260729T090119Z-ecccaf9a`, `claude2-20260729T090239Z-94a953a3`,
  and `claude2-20260729T090341Z-a09b8351`, all exiting with SIGTERM/code 143.
  The supervisor then auto-reassigned ownership from `Claude2` back to `Codex`
  at `2026-07-29T09:04:29Z`, proving the terminal-fallback path also violates
  provider-first L12 routing until #4365 is merged and live-promoted.
- 09:31Z correction: #4365 latest head
  `cbcb4574da48e353e3e33673f81dce5dc13e790d` has all visible Branch CI jobs
  green and auto-merge enabled. A local clean repair commit
  `adcb65105e9daf3124e95962a9a627562debf739` proved the dirty-file blocker,
  but `worker_runner.py` still rejected it because that source SHA was not
  merged into `origin/dev`. Live root was restored to merged SHA
  `a6d56c366f7436574e6d2d241b47564558beac74` at `2026-07-29T09:37Z` so
  worker bootstrap is not globally frozen. #4365 is not live until review,
  merge, and promotion.
- Remaining live gate: the failed Antigravity run at
  `2026-07-29T09:20:19Z` put this review dispatch event into unchanged-task
  cooldown until roughly `2026-07-29T09:35:19Z`; this is not a completed
  review. `L12-VERIFY-OBS-001`, `L12-VERIFY-RUNTIME-001`, and
  `L12-VERIFY-KNOW-001` are also still blocked from Claude2 redispatch by
  failure-loop / chair-reassignment-triage state.
- Scope:
  - supervisor dispatch ordering for `review` tasks;
  - provider quota selection when L12 tasks compete with non-L12 OPS tasks;
  - L12/SUP-L12 provider-first terminal fallback filtering;
  - queue lease cleanup after manual/live priority interruption.
- Acceptance:
  - reproduce the 2026-07-29T08:46Z/08:48Z condition where
    `L12-VERIFY-OBS-001` was in `review` for `Claude2` on PR #4364, but the
    supervisor dispatched `OPS-PROMOTE-PR-CI-TRIGGER-001` to `Claude2` twice;
  - ensure L12 `review` tasks with fresh PR heads and green checks outrank
    non-L12 OPS reviews when they share the only Claude2 quota slot;
  - repeated Claude2/Antigravity terminal or missing-process failures on
    L12/SUP-L12 work cannot auto-reassign owner/reviewer to Codex/Codex2;
  - provider-first fallback still permits Antigravity/Claude-family candidates
    when they are viable;
  - after a non-L12 worker is interrupted for L12 priority, prevent immediate
    redispatch of the same non-L12 queue event ahead of the waiting L12 review;
  - add regression covering queue event ordering, lease cleanup, and provider
    quota accounting;
  - do not edit `.orchestrator/config.json`.

### `SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729`

- Owner: `Claude2`
- Reviewer: `Antigravity`
- Current state: new guard task to materialize after the 11:40Z packet merges.
- Scope:
  - `.orchestrator/supervisor.py` failure-streak/chair-triage dispatch policy;
  - `.orchestrator/state.json` schema handling without ad-hoc runtime edits;
  - `.orchestrator/test_supervisor.py` regression coverage.
- Acceptance:
  - reproduce stale L12 `missing_process` streaks blocking healthy
    Claude2/Antigravity dispatch after a supervisor fix is merged and promoted;
  - add a bounded reaper or eligibility check so stale/cleared failure causes
    cannot keep provider-first rows in chair-triage deadlock;
  - prove the repaired path launches Claude2/Antigravity before any Codex-family
    fallback for L12/SUP-L12 rows;
  - preserve a runtime repair audit trail without manually editing
    `.orchestrator/config.json`;
  - validate with focused unit tests and the full supervisor regression suite.

### `SUP-L12-HELPER-CLAIM-BUSY-PREFERRED-LANE-20260729`

- Owner: `Antigravity`
- Reviewer: `Claude2`
- Current state: new guard task to materialize after the 11:40Z packet merges.
- Scope:
  - helper-claim candidate selection for provider-first L12/SUP-L12 rows;
  - busy preferred-provider behavior when Claude2 or Antigravity is already
    running an eligible row;
  - Codex/Codex2 fallback admission tests.
- Acceptance:
  - reproduce the 2026-07-29T11:34Z case where Claude2 was busy on
    `L12-VERIFY-OBS-001` and SUP-L12 helper claims still launched Codex2;
  - change policy so busy preferred providers cause wait/defer or
    Claude/Antigravity-family fallback before Codex-family fallback;
  - prove Codex/Codex2 cannot claim provider-first L12/SUP-L12 work merely
    because the preferred provider is temporarily busy;
  - keep non-L12 emergency fallback behavior intact where explicitly allowed;
  - do not edit `.orchestrator/config.json`.

### `SUP-L12-RUNNING-OWNER-RECONCILE-20260729`

- Owner: `Claude2`
- Reviewer: `Antigravity`
- Current state: new guard task to materialize after the 11:40Z packet merges.
- Scope:
  - authoritative task rows versus live `worker_runner.py` processes;
  - owner/reviewer changes after failed helper/fallback runs;
  - task brief and evidence row reconciliation.
- Acceptance:
  - reproduce row ownership/reviewer changes while a live or recently terminal
    worker still exists for the same canonical task;
  - add a reconciliation check that reports exact row owner, reviewer, status,
    run id, PID, exit code, and source SHA before claiming fleet health;
  - prevent stale fallback failures from overwriting provider-first owner truth
    without a recorded reason;
  - archive an evidence table for active rows and terminal runs;
  - do not edit `.orchestrator/config.json`.

### `SUP-L12-LONG-FINALIZE-LEASE-20260729`

- Owner: `Antigravity`
- Reviewer: `Claude2`
- Current state: new guard task to materialize after the 11:40Z packet merges.
- Scope:
  - long-running finalize/review worker detection;
  - provider quota accounting when non-L12 OPS/finalize work competes with L12;
  - supervisor status/readback messages.
- Acceptance:
  - reproduce a long owner/reviewer/finalize run consuming a provider slot while
    L12 review/owner work is eligible;
  - add status/readback that distinguishes healthy long-running work from a
    stuck lease that masks L12 readiness;
  - prove L12 provider-first priority still applies after lease expiry,
    completion, interruption, or terminal cleanup;
  - do not terminate healthy workers as part of the test unless the test owns
    them in an isolated fixture;
  - do not edit `.orchestrator/config.json`.

## Dispatch order

1. Prioritize `L12-VERIFY-OBS-001` Claude2 review for PR #4364 before any
   non-L12 OPS Claude2 review.
2. Do not count the failed/preempted KNOW runs as delivery; preserve their logs
   and redispatch KNOW only when `Claude2` can run it without another
   higher-priority preemption.
3. Redispatch RUNTIME only after OBS review no longer needs the sole Claude2
   slot; the last RUNTIME run was superseded before terminal evidence.
4. Keep LEARN blocked until a new implementation proves real service-boundary
   evidence rather than pass literals.
5. Keep OBS in `review` until `Claude2` performs exact review of PR #4364 head
   `ffd90cab757ee3939cbf7c4e5e5c7956f29f0bdd`.
6. Keep legacy `SUP-L12-FAKE-VERIFIER-GATE-20260729` and
   `SUP-L12-LIVE-WORKER-RECON-20260729` out of the active board unless they can
   run on Claude2/Antigravity, never on Codex/Codex2.
7. Materialize the new `SUP-L12-*` guard tasks above through the
   supervisor/auto-worker path with Claude2/Antigravity-first ownership.
8. Treat any Codex/Codex2 helper claim on those provider-first rows as a
   runtime bug to be captured, not as valid fleet progress.
9. Dispatch FE only with strict live-BFF evidence and no swallowed-failure UI.
10. Dispatch HOSTED, then CLOSE.

## Current fleet health checkpoint

Observed after the 08:20Z rescue and updated at 11:40Z:

- `Claude2` can launch governed auto workers, but `L12-VERIFY-KNOW-001` failed
  as `claude2-20260729T083251Z-33cfa39f` and returned to `todo`.
- `L12-VERIFY-RUNTIME-001` briefly ran as
  `claude2-20260729T083911Z-b350d04f`, then was superseded back to `todo`.
- `L12-VERIFY-OBS-001` is now `review` on PR #4364 head
  `ffd90cab757ee3939cbf7c4e5e5c7956f29f0bdd`; all visible PR checks are green,
  but branch protection is `BLOCKED` and no Claude2 review evidence exists yet.
- `Claude2` was instead redispatched to non-L12
  `OPS-PROMOTE-PR-CI-TRIGGER-001` as
  `claude2-20260729T084759Z-b06bd849` after an attempted live priority
  interruption, proving the dispatch priority bug is durable.
- `Antigravity` can launch and move governed tasks; after #4364 handoff it was
  also dispatched back to `SUP-L12-FLEET-RUNTIME-RELIABILITY-20260729`.
- `SUP-L12-FLEET-RUNTIME-RELIABILITY-20260729` proved Antigravity owner
  dispatch by reaching `review`, but the reviewer was auto-reassigned from
  `Codex2` to `Codex` after repeated Codex2 terminal failures. This is an
  observed provider-first violation until #4362 merges and is validated in the
  live command root.
- `Claude2` account quota is effectively `1/1`; OBS review, RUNTIME, KNOW, and
  non-L12 OPS review currently compete for it, and the ordering is wrong.
- `Antigravity` account quota is effectively `1/1`, so OBS/guard work must be
  serialized unless the account policy changes.
- Runtime deps were temporarily rescued in `/tmp/l12-alpha-pydeps`; this is
  not durable completion and must be formalized by
  `SUP-L12-WORKER-PYDEPS-20260729`.

09:31Z update:

- #4365 durable PR head is now
  `cbcb4574da48e353e3e33673f81dce5dc13e790d`; all visible Branch CI jobs are
  green and auto-merge is enabled, but independent Antigravity review remains
  outstanding.
- Temporary local commit `adcb65105e9daf3124e95962a9a627562debf739` removed
  the dirty `.orchestrator/supervisor.py` condition but was rejected by
  worker_runner because it was not merged into `origin/dev`.
- Live root was restored to merged SHA
  `a6d56c366f7436574e6d2d241b47564558beac74` at `2026-07-29T09:37Z`; #4365
  remains PR-only until independent review, merge, and promotion.
- The failed `antigravity1-1` review worker at `2026-07-29T09:20:19Z` failed
  before model work due to the dirty-command-root guard and only created a
  cooldown blocker; it is not review evidence.
- Current dispatch blockers are factual and still open:
  - `SUP-L12-REVIEW-PRIORITY-GATE-20260729`: waiting for cooldown expiry and
    Antigravity exact review of #4365 `cbcb4574...`;
  - `L12-VERIFY-OBS-001`: Claude2 reviewer is blocked by failure-loop /
    chair-reassignment-triage state;
  - `L12-VERIFY-RUNTIME-001` and `L12-VERIFY-KNOW-001`: Claude2 owner work is
    blocked by the same failure-loop / chair-reassignment-triage state.

11:40Z update:

- #4371 merged and live-promoted the priority/preemption repair as
  `c1e396495d37a1c9dfeea5704e7eb73db6acde0e`; supervisor restarted
  intentionally as PID `4191254`, with no config diff.
- Human/Ops cleared stale Claude2/L12 `missing_process` streaks as a temporary
  live repair; the durable missing development is
  `SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729`.
- `Claude2` launched `claude2-20260729T113336Z-08eddb2f` on
  `L12-VERIFY-OBS-001`, proving the fleet path can run after the stale-streak
  repair.
- `Antigravity` completed `OPS-PROMOTE-PR-CI-TRIGGER-001` as
  `antigravity1-1-20260729T112638Z-2b127a26`.
- Helper claims still sent SUP-L12 work to Codex2 while Claude2 was busy, so
  `SUP-L12-HELPER-CLAIM-BUSY-PREFERRED-LANE-20260729`,
  `SUP-L12-RUNNING-OWNER-RECONCILE-20260729`, and
  `SUP-L12-LONG-FINALIZE-LEASE-20260729` are now required guard work.

## Final audit result

Not complete. The immediate next work is:

1. Finish FE product proof or keep `L12-FE-TRUTH-001` blocked.
2. Clear the supervisor cooldown/failure-loop/chair-triage gates without
   editing `.orchestrator/config.json`, then dispatch Antigravity/Claude2
   workers again.
3. Restart Runtime verifier when Claude2 can actually run.
4. Restart Knowledge verifier when Claude2 can actually run.
5. Replace Learning verifier fake proof with real service-boundary proof.
6. Complete Observability review/repair with real durable readback proof.
7. Only then run Hosted and Closeout.
