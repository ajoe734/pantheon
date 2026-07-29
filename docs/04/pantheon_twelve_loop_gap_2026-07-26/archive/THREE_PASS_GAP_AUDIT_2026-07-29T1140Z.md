# Twelve-loop gap audit refresh — runtime/fleet recovery addendum

Captured at: `2026-07-29T11:40Z`

Program: `pantheon-twelve-loop-gap-2026-07-26`

This addendum supersedes the 2026-07-29T10:25Z dispatch snapshot for live
fleet facts. It does not supersede the loop-by-loop product gap inventory:
the twelve loops are still not product-operable as a set.

The user-facing distinction is important:

- `supervisor/auto-worker can run` is now partially proven.
- `Claude2/Antigravity-first dispatch is stable` is not yet fully proven.
- `all twelve loops work` is still false.

## Authoritative evidence inspected

- Live status root: `/home/lupin/pantheon`.
- Live command root: `/home/lupin/pantheon-ci-deploy/dev-root`.
- Live supervisor config:
  `/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json`.
- #4371 `SUP-L12-WAVE0-PREEMPTION-PROTECT-20260729` merged at
  `2026-07-29T11:23:46Z`, merge commit
  `c1e396495d37a1c9dfeea5704e7eb73db6acde0e`.
- Live dev-root promoted to `c1e396495d37a1c9dfeea5704e7eb73db6acde0e`.
- `.orchestrator/config.json` diff after promotion: `0`.
- Supervisor restart was an intentional deploy restart, new PID `4191254`.
- Watchdog after restart: `reason=supervisor_healthy`, active root
  `/home/lupin/pantheon-ci-deploy/dev-root`, worker-runner root not split.
- Runtime live repair, no config change:
  `.orchestrator/state.json.bak-human-ops-clear-l12-stale-failure-streaks-20260729T1133Z`
  was created before clearing stale Claude2/L12 `missing_process` streaks.
- Worker facts after stale streak repair:
  - `claude2-20260729T113336Z-08eddb2f` running
    `L12-VERIFY-OBS-001`.
  - `codex-20260729T112836Z-a834f1cb` running
    `OPS-PROMOTE-PR-CI-TRIGGER-001` owner finalize.
  - `antigravity1-1-20260729T112638Z-2b127a26` completed
    `OPS-PROMOTE-PR-CI-TRIGGER-001` review successfully.
  - `codex-20260729T113504Z-3f621b50` and
    `codex-20260729T113520Z-78d3fc84` attempted SUP-L12 fallback work and
    failed with signal `15` / exit `143`.
- Current L12/SUP-L12 rows at this capture:
  - `L12-VERIFY-OBS-001`: `review`, owner `Antigravity`, reviewer `Claude2`;
    live Claude2 review worker running.
  - `L12-VERIFY-KNOW-001`: `todo`, owner `Claude2`, reviewer `Antigravity`.
  - `L12-VERIFY-RUNTIME-001`: `todo`, owner `Claude2`, reviewer
    `Antigravity`.
  - `L12-VERIFY-LEARN-001`: still blocked by fake-verifier history.
  - `L12-FE-TRUTH-001`: still blocked.
  - `SUP-L12-STALE-PR-RETIRE-20260729`: `todo`, owner `Antigravity`,
    reviewer `Claude2` after failed Codex2 fallback.
  - `SUP-L12-MERGED-ROW-RECONCILE-20260729`: `todo`, owner `Claude2`,
    reviewer `Antigravity`.
  - `SUP-L12-FLEET-DISPATCH-READBACK-20260729`: `todo`, owner `Antigravity`,
    reviewer `Claude2` after failed Codex2 fallback.

## Executive verdict

The system has moved forward but is not complete.

Fixed since 10:25Z:

1. Preferred-lane helper claim fixes from #4368/#4369 are merged.
2. Ordinary owned-backlog preemption fix from #4370 is merged.
3. SUP-L12 Wave 0 preemption protection from #4371 is merged, promoted live,
   and observed: Claude2 can now run L12 review work without immediate
   priority-preemption SIGTERM.
4. Stale runtime failure streaks that blocked Claude2 L12 admission were
   cleared as a scoped live repair with backup and audit note.

Still missing:

1. Product verifiers and hosted proof remain incomplete for KNOW, LEARN,
   RUNTIME, OBS, FE, HOSTED, and CLOSE.
2. Failure-loop / chair-triage stale streak policy still requires durable code
   hardening; the 11:33Z clear was runtime-state rescue, not a repository fix.
3. Helper-claim still falls to Codex2 when Claude2 is busy, even for SUP-L12
   provider-first Wave 0 work. The fallback workers then failed and forced row
   churn.
4. Running-worker / task-row ownership reconciliation remains weak: after
   helper claim and terminal failure, rows can move while worker records still
   exist or report failed.
5. Long finalize worker handling remains a fleet pressure point:
   `OPS-PROMOTE-PR-CI-TRIGGER-001` was still running for Codex while L12 lanes
   were being recovered.

## Pass 1 — product-loop development gap inventory

This pass ignores fleet mechanics and asks only whether the twelve loops are
operable as product loops.

| Loop | Current verdict | Missing development | Missing verification |
|---|---:|---|---|
| Source Ingestion | Not proven | Persona requirement to durable `SourceRecord`; provider failure and restart behavior. | Store readback by id; duplicate and failed-provider evidence; BFF/controller terminal truth. |
| Strategy Distillation | Not proven | `SourceRecord` to mutable `StrategySpec`; immutable-approved gate. | Before/after readbacks; unapproved/immutable negative cases. |
| Alpha Replication | Not proven | Approved `StrategySpec` to authoritative `ExperimentRun`; registry/research failure path. | Experiment readback; restart/replay proof; failure terminal truth. |
| Persona Teaching | Not proven; fake-proof history contradicts completion | Training/session/eval boundary and persona update persistence. | Eval gate, before/after persona readback, tenant/RBAC negative. |
| Agora Interaction Evidence | Not proven; fake-proof history contradicts completion | Command to tenant-scoped `DatasetVersion` and handoff. | Dataset/handoff ids, duplicate, restart/DLQ readbacks. |
| Human Imitation Shadow Evaluation | Not proven; fake-proof history contradicts completion | Real dataset to gated `ShadowImitationCandidate`; no seed fallback. | Candidate readback, seed fallback rejection, tenant bypass negative. |
| Consultation | Not proven; fake-proof history contradicts completion | Consultation memo and governance handoff persistence. | Memo/handoff ids, duplicate/restart/DLQ evidence, no runtime mutation. |
| Promotion Deployment | Not proven | Approved artifact to `DeploymentPlan`, `RuntimeBinding`, governed paper worker. | Binding correlation; duplicate/crash-after-side-effect rejection. |
| Capital Pool Execution | Not proven | Paper-only signal/order/fill/position/heartbeat pipeline; kill/pause/retire controls. | No-live-capital proof; scope rejection; restart convergence; BFF truth. |
| Telemetry Reconciliation | In review, not accepted | Real telemetry/drift/incident/postmortem/evolution/action boundary calls. | Persisted ids from services, heartbeat/order/drawdown correlation. |
| Evolution | In review, not accepted | Incident to postmortem to governed `EvolutionDecision` to terminal action receipt. | Retry/compensation evidence; approved-action receipt; negative no-go path. |
| BFF Health Monitoring | Not proven | Downstream stop/recovery telemetry, strict-live frontend rendering, hosted identity. | Browser network evidence, FE/BFF manifest, 1440/390 DOM, axe/keyboard/reduced-motion. |

Pass 1 conclusion: no additional loop is operable yet. OBS is actively being
reviewed by Claude2, but review in progress is not acceptance.

## Pass 2 — evidence, PR, and validation gap inventory

This pass checks whether the evidence chain is strong enough to support a
completion claim.

Accepted control-plane evidence:

- #4368 proved helper claim now respects preferred lane order in the ordinary
  case.
- #4369 proved preferred fallback lanes are used when owner fallback config is
  incomplete.
- #4370 stopped ordinary owned backlog from killing already-running workers.
- #4371 protects SUP-L12 Wave 0 recovery workers from priority preemption and
  is live at `c1e396495d37a1c9dfeea5704e7eb73db6acde0e`.

Rejected or incomplete evidence:

- LEARN remains blocked because prior verifier heads were pass-printer or
  self-attesting proofs.
- OBS remains only in review; PR #4364 must still be current, exact-reviewed,
  merged, and archived before it counts.
- Runtime failure-streak clearing at 11:33Z is valid live rescue but not a
  code-level guardrail.
- The Codex2 fallback attempts on SUP-L12 Wave 0 failed with exit `143`; they
  cannot be counted as completed fleet work.
- `OPS-PROMOTE-PR-CI-TRIGGER-001` is unrelated finalize pressure and cannot be
  counted as twelve-loop product progress.

Missing validation:

- Full verifier service-boundary tests for KNOW/LEARN/RUNTIME/OBS.
- Durable evidence manifests with concrete ids, checksums, and replay inputs.
- Negative tests for tenant/RBAC, duplicate, restart/replay, DLQ, and no live
  capital.
- Hosted strict-live browser proof for execute-plans.
- Independent exact-head review and merge/archive for each product lane.
- Regression tests for stale failure streak reaping and provider-first
  helper-claim under busy preferred owners.

Pass 2 conclusion: the evidence is still incomplete. The new control-plane
repairs are useful prerequisites, not product completion.

## Pass 3 — supervisor/fleet gap inventory

This pass checks whether the user-requested fleet behavior is true:
supervisor/auto-worker dispatch, Claude/Antigravity priority where possible,
parallelization, and real completion.

What is now proven:

- Work is being dispatched to real supervisor/auto-workers, not Codex chat
  subagents.
- Antigravity completed one real review worker.
- Claude2 was admitted and is running L12 review work after stale streak
  cleanup.
- Supervisor stays healthy with two live worker runners.
- #4371 stopped the previous SUP-L12 priority-preemption kill loop.

What is still broken:

- Stale `task_failure_streaks` can block otherwise healthy Claude2 L12 work
  until manual runtime cleanup.
- Helper-claim still moved SUP-L12 Wave 0 work from Claude2 to Codex2 while
  Claude2 was busy with L12 review.
- Codex2 fallback workers for SUP-L12 immediately failed with signal `15` /
  exit `143`.
- Provider-first proof is therefore mixed: Claude2/Antigravity can run, but
  dispatcher fallback can still drift to Codex-family lanes.
- There is no durable closeout yet proving that the Wave 0 cleanup tasks
  completed.

Pass 3 conclusion: fleets are operational but not yet reliably draining the
requested work. The next work must harden stale-failure and helper-claim
policy, then redispatch Wave 0 and product lanes through supervisor.

## Required execution tasks

The following tasks must be present in the execution packet and dispatched via
real supervisor/auto-workers.

### Fleet/control-plane guardrails

1. `SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729`
   - Purpose: turn the 11:33Z live rescue into durable policy.
   - Acceptance:
     - reproduce stale `missing_process` streaks blocking healthy Claude2 L12
       dispatch;
     - expire or clear streaks after merged preemption fixes and healthy
       provider probes;
     - record manual live repairs without requiring ad-hoc JSON editing;
     - regression proves L12 review/owner dispatch resumes without Codex
       chair triage when Claude2/Antigravity are viable.

2. `SUP-L12-HELPER-CLAIM-BUSY-PREFERRED-LANE-20260729`
   - Purpose: stop helper-claim from falling to Codex2 merely because Claude2
     is busy with another L12 task.
   - Acceptance:
     - reproduce SUP-L12 Wave 0 rows moving from Claude2 to Codex2 while
       Claude2 is running `L12-VERIFY-OBS-001`;
     - preferred lanes remain provider-first when the owner is busy but healthy;
     - Codex/Codex2 fallback requires explicit terminal unavailability or
       exhausted provider-first lanes;
     - regression covers `preferred_lane_order`, active owner load, and helper
       fallback.

3. `SUP-L12-RUNNING-OWNER-RECONCILE-20260729`
   - Purpose: prevent row/worker truth drift after helper claim or fallback
     failure.
   - Acceptance:
     - detect running worker on a task whose owner/reviewer changed;
     - reconcile by finishing, superseding, or requeueing exactly once;
     - no duplicate worker per task;
     - evidence names exact run id, queue event, task row before/after.

4. `SUP-L12-LONG-FINALIZE-LEASE-20260729`
   - Purpose: keep unrelated long finalize work from masking L12 fleet
     readiness.
   - Acceptance:
     - detect owner-finalize workers that run beyond expected closeout time;
     - record progress or blocker without monopolizing dispatch truth;
     - supervisor can continue independent Claude2/Antigravity L12 work in
       parallel.

### Current Wave 0 recovery tasks

5. `SUP-L12-STALE-PR-RETIRE-20260729`
   - Owner/reviewer should remain Antigravity/Claude2 unless a provider-first
     terminal failure is current and proven.

6. `SUP-L12-MERGED-ROW-RECONCILE-20260729`
   - Owner/reviewer should remain Claude2/Antigravity.

7. `SUP-L12-FLEET-DISPATCH-READBACK-20260729`
   - Owner/reviewer should remain Antigravity/Claude2 unless provider-first
     terminal failure is current and proven.

### Product lanes

8. `L12-VERIFY-KNOW-001`
9. `L12-VERIFY-LEARN-001`
10. `L12-VERIFY-RUNTIME-001`
11. `L12-VERIFY-OBS-001`
12. `L12-FE-TRUTH-001`
13. `L12-HOSTED-001`
14. `L12-CLOSE-001`

The product lanes remain as documented in the 10:25Z audit. They must not be
closed from control-plane improvements alone.

## Completion boundary

The goal is not complete until:

1. this addendum and execution packet are merged to `dev`;
2. guardrail tasks are dispatched to real supervisor/auto-workers;
3. Wave 0 recovery tasks reach terminal accepted evidence or explicit blockers;
4. all product verifier lanes are implemented, reviewed, merged, and archived;
5. hosted proof and final closeout pass; and
6. no `.orchestrator/config.json` mutation is used as an unreviewed shortcut.
