# 2026-04-18 Current State Reconciliation

Record layer document.
Do not treat this file as canonical blueprint truth.
Use `ai-status.json`, `current-work.md`, and L1/L2 canonical documents as the primary sources.

## Purpose

This note reconciles an interim Claude status summary against the current repository state on 2026-04-18.

The goal is to separate:

- what was directionally correct in the interim summary
- what is now stale
- what remains genuinely open
- where the tracking layers disagree with each other

## Verified Current Sources

- `ai-status.json`
- `current-work.md`
- `.coordination/reviews/BFF-FIX-001-review.md`
- `.coordination/reviews/UI-CLOSE-001-review.md`
- `OSS_INTEGRATION_CHECKLIST.md`
- `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`
- `.coordination/reviews/DEPTH-CAP002-review.md`
- `.coordination/reviews/BP5-SVC-005-review.md`

## What The Interim Summary Got Right

1. The infrastructure baseline is largely landed.
   Single-VM and dual-VM deployment evidence exists, and the repo has a broad service footprint.

2. The repo is not "fully complete" just because the baseline exists.
   Product closure, workbench completeness, and execution-proof maturity are still unfinished.

3. Task tracking is thinner than the real remaining backlog.
   The active `ai-status.json` task list is much smaller than the amount of unfinished product and closeout work implied by `current-work.md` and the canonical backlog.

## What Is Now Stale

### 1. The "5 open BFF gaps" claim is stale

That is no longer the current truth.

`BFF-FIX-001` review explicitly says the five acceptance items are satisfied and the original five-gap closure scope is complete.

The reviewed closure covers:

- `PKT-002-incident-action-drawer`
- `PKT-002-incident-detail`
- `PKT-002-incident-home`
- `PKT-003-post-incident-review`
- `PKT-004-capital-binding-drilldowns`

Residual runtime-data follow-up was split separately and does not reopen the BFF-gap task.

### 2. The "statsmodels / QuantLib / vectorbt still need Gate 2 evidence" claim is stale

`OSS_INTEGRATION_CHECKLIST.md` now records:

- `vectorbt`: `governed`
- `statsmodels`: `governed`
- `QuantLib`: `governed`

Their repo-local evidence packs now exist under `integrations/*/{integration,governance,smoke_test}.md`.

### 3. The "PKT-006~009 ui-done packets are still null / unacknowledged" claim is stale

`UI-CLOSE-001` review shows:

- `PKT-006`: `closed` / `loop-complete`
- `PKT-007`: `acknowledged` / `blocked`
- `PKT-008`: `closed` / `loop-complete`
- `PKT-009`: `closed` / `loop-complete`

So these packets are not sitting in an unacknowledged null state anymore.

### 4. The "8 new todo tasks were added to ai-status.json" claim is stale

Current `ai-status.json` does not contain that expanded task set.

The active task board currently contains only:

- `DEPTH-EVO004` (`review_approved`)
- `DEPTH-EVO005` (`todo`)
- `LUV-REVIEW-015` (`done`)

This means the earlier 8-task expansion was an intermediate state, not the current active task truth.

## Verified Current Truth

### 1. Active execution tracking is very narrow

`ai-status.json` currently tracks only two still-open execution items:

- `DEPTH-EVO004`
- `DEPTH-EVO005`

This is much narrower than the broader unfinished surface implied by `current-work.md`.

### 2. Current feature-level coordination is broader than the task board

`current-work.md` currently reports:

- `Tracked features: 26`
- `Waiting for Lovable/front-end: 2`
- `UI-done returned: 25`
- `Frontend feedback returned: 20`
- `Open BFF gaps: 0`

That means the product surface is still active, but the task board is not fully materializing that remaining work.

### 3. Wave 2 operator work is partly still in closeout, not fully closed

Current coordination stages show:

- `PKT-011-health-status-board`: `ui_done_received`
- `PKT-012-alerts-rail`: `ui_done_received`
- `PKT-013-operator-home`: `ui_done_received`
- `PKT-014-paper-live-drift`: `ui_done_received`

Those are no longer blocked on missing Pantheon contract packets, but they are also not closed loops yet.

### 4. Consultation and Knowledge were not still at raw spec-request state

The current truth is:

- `PKT-consultation-workbench`: `waiting_for_lovable`
- `PKT-knowledge-workbench`: `frontend_feedback_received`

So those workbenches have moved past the earlier "no packet yet" state, although they are not fully complete products.

### 5. Some deep implementation work already has acceptance evidence

The interim summary implied several canonical workstreams had never really gone through acceptance.

That is too broad.

Examples of already-reviewed depth work include:

- deployment orchestration saga (`BP5-SVC-005`) approved
- multi-persona synthesis (`DEPTH-CAP002`) approved

This does not prove every canonical workstream is fully closed, but it does show that some of the cited "never accepted" areas already have explicit review evidence.

### 6. Execution proof maturity is still below governed paper execution

The repo now has a canonical maturity ladder.

By that ladder:

- many routes and contracts are at `EP1`
- several composed local slices are at `EP2`
- single-VM and dual-VM evidence support `EP3`
- the repo still does not have stable `EP4` governed paper execution proof
- the repo still does not have `EP5` canary/live proof

So the infrastructure baseline is real, but the runtime-proof claim must remain below final execution completeness.

## Real Remaining Gaps

### 1. Tracking-layer drift

The biggest governance problem is not "missing BFF fixes" anymore.
It is that the tracking layers no longer line up cleanly.

Examples:

- `current-work.md` shows a large set of features in `frontend_feedback_received` or `ui_done_received`
- `ai-status.json` only carries two open execution tasks
- agent metadata in `ai-status.json` still references older auto-dispatch messages such as `RUNTIME-FIX-001` and `DEPTH-CAP002-SIDECAR-REVIEW`, even though those are not active tasks in the current task list

### 2. Front-end closeout and replayability truth

Many packets are no longer blocked on Pantheon contract gaps, but are still awaiting truthful front-end replay, review closeout, or bundle normalization.

This is especially visible in the large cluster of `frontend_feedback_received` rows in `current-work.md`.

### 3. Wave 2 operator closeout

`PKT-011` through `PKT-014` are not stuck on missing backend contract work now, but they still need Pantheon/front-end closeout to move from `ui_done_received` to a settled disposition.

### 4. Workbench product completeness

Consultation and Knowledge now have contract packets, but the workbench surfaces themselves are not fully complete products.

This remains part of the broader productization backlog, not a pure contract-gap problem.

### 5. Execution proof maturity

The repo still needs the work required to climb from `EP3` to `EP4`:

- stable runtime auth and authority path
- truthful paper-runtime loop
- governance + telemetry + rollback evidence in one integrated proof

## Concrete Current Risks

1. The repo can look more complete or less complete than it really is, depending on whether someone reads `current-work.md`, `ai-status.json`, or older review chat without reconciliation.

2. Some orchestration/issue automation still fails on GitHub label handling.
   `current-work.md` shows repeated issue-update failures caused by missing `'feedback-ready'` label.

3. `current-work.md` discussion-planning and agent sections still carry some stale execution chatter from prior materializations, even though the active task board is much smaller now.

## Practical Interpretation

The most accurate short reading of current repo state is:

- baseline infrastructure: largely landed
- original 5 BFF gaps: closed
- OSS Gate 2 evidence for `statsmodels` / `QuantLib` / `vectorbt`: landed
- UI closeout for `PKT-006~009`: materially updated, not null
- active product/backlog work: still real
- task-board materialization: too sparse for the true remaining surface
- runtime proof: still below governed paper execution

## Recommended Next Reconciliation Work

1. Re-materialize the remaining real backlog into explicit active tasks instead of relying on `current-work.md` rows alone.
2. Sweep `PKT-011~014` to final reviewed disposition.
3. Continue workbench productization for Consultation and Knowledge beyond overview packets.
4. Clean stale agent/task metadata so `ai-status.json` and `current-work.md` stop implying different operational realities.
5. Treat `EP4` proof work as a separate execution program, not as a side effect of route completion.
