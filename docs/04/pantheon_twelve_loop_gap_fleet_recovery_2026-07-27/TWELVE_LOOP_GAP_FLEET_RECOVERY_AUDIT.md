# Twelve-loop gap and fleet recovery audit — 2026-07-27

Status: dispatchable gap packet, not completion evidence

Recorded at: 2026-07-27T20:39:49Z

This document answers the operator question: why the twelve canonical loops
still cannot be called normally operational, what development and validation
work is still missing, and what must be dispatched to real supervisor-managed
auto-workers next. It deliberately does not mark any loop as done from local
tests, PR-green status, planning notes, subagent output, or stale review.

Authoritative inputs checked for this cut:

- `docs/deployment/loop-catalog.registry.json`
- live `/home/lupin/pantheon/ai-status.json`
- live `/home/lupin/pantheon/.orchestrator/state.json`
- live `/home/lupin/pantheon/ai-activity-log.jsonl`
- GitHub PR state for #4193, #4267, #4269, #4273, #4274
- execution packet `docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/`
- current dirty shared checkout status, confirming this audit was prepared from
  a clean task worktree rather than by editing the shared live checkout

## Executive conclusion

The twelve loops are not fully usable yet. The current hard blockers are not a
single missing test; they are layered:

1. Product loop truth is still below target: the catalog reports eleven loops
   at `api-only` and Capital Pool Execution at `manual`; none is proven
   `reconciled` or `proven-live`.
2. Delivery gates are not closed: the currently relevant L12 PRs are still open
   and GitHub reports `REVIEW_REQUIRED`; two important PRs are also `BEHIND`.
3. Evidence and status planes disagree: #4273 was repaired and pushed to
   `141d06ec5d1aa5b0ea7d1b7bdc148ad28060a443`, but live status rows still
   regressed to the older `f6d340ff018cc178bcf2023b7fae00cde77ebb2c` /
   `in_progress` state.
4. Fleet operation is partially functioning but unstable: supervisor dispatches
   real auto-workers, but Claude lanes are paused or quota-limited, sidecar
   auto-dispatch is disabled, some worker records are later reconciled as
   missing processes, and review results do not reliably land as GitHub review
   gate evidence.
5. Hosted/product acceptance has not happened. Green local or PR CI checks only
   prove isolated contract slices; they do not prove the twelve live loops are
   continuously owned, restart-safe, surfaced honestly, and safe.

## Canonical loop maturity inventory

The catalog currently exposes exactly twelve L1 loop ids:

| # | Loop id | Catalog title | Current maturity | Target |
|---:|---|---|---|---|
| 1 | `source_ingestion` | Source Ingestion | `api-only` | `reconciled` |
| 2 | `strategy_distillation` | Strategy Distillation | `api-only` | `reconciled` |
| 3 | `alpha_replication` | Alpha Replication | `api-only` | `reconciled` |
| 4 | `persona_teaching` | Persona Teaching | `api-only` | `reconciled` |
| 5 | `agora_interaction_evidence` | Agora Interaction Evidence | `api-only` | `reconciled` |
| 6 | `human_imitation_shadow_evaluation` | Human Imitation / Shadow Evaluation | `api-only` | `reconciled` |
| 7 | `consultation` | Consultation | `api-only` | `reconciled` |
| 8 | `promotion_deployment` | Promotion / Deployment | `api-only` | `reconciled` |
| 9 | `capital_pool_execution` | Capital Pool Execution | `manual` | `reconciled` |
| 10 | `telemetry_reconciliation` | Telemetry / Reconciliation | `api-only` | `reconciled` |
| 11 | `evolution` | Evolution | `api-only` | `reconciled` |
| 12 | `bff_health_monitoring` | BFF Health Monitoring | `api-only` | `reconciled` |

This means the default product statement is still: "the loops exist as
contracts and partial APIs, but are not yet proven as automatic reconciled
product loops."

## Round 1 audit — product-loop gap pass

Round 1 starts from the product contract: a loop is usable only when there is a
real controller or worker that owns the loop, produces canonical effects or a
terminal governed failure, survives restart/replay/idempotency drills, and is
honestly represented to the operator.

### R1 findings

| Gap | Affected loops | Missing development | Missing validation |
|---|---|---|---|
| L12-R1-001: no reconciled catalog truth | all 12 | promote loop truth only after the controller/worker/evidence stack exists; do not edit registry maturity first | loop-health/inventory tests must reject registry-only or local snapshot proof |
| L12-R1-002: continuous worker manifest incomplete | all worker-owned loops | manifest every required scheduler/consumer/reconciler process, env, volume, restart policy, health endpoint, and auth boundary | compose/dev VM readback plus restart drill per worker |
| L12-R1-003: knowledge pipeline not proven end-to-end | Source, Distillation, Alpha | connector freshness, normalized source ingestion, draft strategy creation, dedupe, queue ownership | repo-root and foreign-cwd tests; durable replay; duplicate source negative; PR/hosted readback |
| L12-R1-004: learning pipeline not proven end-to-end | Teaching, Agora, Imitation, Consultation | async evaluator ownership, interaction-to-dataset extraction, shadow eval scheduling, consultation workflow outbox consumption | tenant/auth negatives, replay idempotency, no fake memo/success, restart recovery |
| L12-R1-005: runtime/deployment path not proven safe | Deployment, Capital | plan-to-apply saga ownership, RuntimeBinding reconciliation, paper fleet worker identity, stop/restart isolation | Redis/process restart, no-live-capital proof, rollback drill, exact worker runtime readback |
| L12-R1-006: observation/evolution/BFF chain incomplete | Telemetry, Evolution, BFF | downstream health monitor, reconciliation drift, incident/postmortem/evolution handoff, retained delivery counters | service-JWT tests, incident 409 failure semantics, DLQ/retry, delivery retention, hosted truth |
| L12-R1-007: hosted operator truth absent | all 12 | frontend truth panel must show desired/controller/failure/actual/provenance without overstating maturity | Browser smoke against Pantheon-owned FE/BFF with strict fallback and safe writes |

### R1 consequence

The product gap remains open even when individual task PRs are CI-green. A loop
slice can be locally correct but still unusable if it has no admitted worker,
no restart proof, no hosted truth, or no merged exact-head review.

## Round 2 audit — delivery, evidence, and PR gate pass

Round 2 starts from delivery evidence: branch, commit, push, PR, exact-head CI,
independent review, merge, archive, and closeout all have to exist. Current PR
state is not sufficient.

### R2 findings

| PR/task | Current evidence | Missing before completion |
|---|---|---|
| #4273 `OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001` | PR head is `141d06ec5d1aa5b0ea7d1b7bdc148ad28060a443`; visible Branch CI is green; local evidence gate was repaired to 13 tests | GitHub review gate still says `REVIEW_REQUIRED`; live status still records older `f6d340ff...`; needs exact-head reviewer decision, merge, archive closeout |
| #4274 `L12-BFF-001` | PR head is `caef48af71178e22ff38e8afca7445ffa91b5d77`; visible Branch CI is green; incident 409 semantics and retained delivery counts were repaired | GitHub review gate still says `REVIEW_REQUIRED`; needs exact-head review, merge, archive closeout |
| #4269 `L12-CURRENT-GAP-FLEET-AUDIT-20260727` | PR head is `f9a4f8e173bbcd58819cddc72c1a5638a4c819df`; activity log shows a Codex2 approval event | GitHub `latestReviews` is empty and review gate says `REVIEW_REQUIRED`; owner closeout worker was later reconciled as missing; needs GitHub review bridge or formal review, merge, closeout |
| #4267 `L12-EVO-001` | task row is `review`; PR exists | PR is `BEHIND`; needs compose to current `dev`, exact-head CI, review, merge |
| #4193 `L12-DIST-001` | task row is `review`; PR exists | PR is `BEHIND` and task source_ref is not bound; needs compose, exact-head CI, review, merge |
| downstream L12 verification tasks | task rows exist for manifest/truth/FE/verification/hosted/closeout | dependencies are not done; they must not run as completion proof until prerequisite PRs and fleet controls close |

### R2 consequence

The missing work includes both development and governance. Review notes inside
`ai-activity-log.jsonl` are useful evidence, but they are not the same as a
GitHub review satisfying branch protection. A green check rollup without a
review/merge/closeout leaves the loop in limbo.

## Round 3 audit — fleet operation and dispatch pass

Round 3 starts from the user's operational requirement: work must be dispatched
to supervisor/auto-worker fleets, preferably Claude and Antigravity when
healthy, not to ad hoc Codex subagents. Current state shows real fleet movement
but not healthy completion.

### R3 findings

| Gap | Evidence | Impact | Required repair |
|---|---|---|---|
| L12-R3-001: Claude / Antigravity preferred lanes unavailable | activity log shows Claude quota/paused and Claude2 disabled; no active Antigravity lane was available in this audit window | supervisor falls back to Codex/Codex2 even when user wanted Claude/Antigravity first | record lane health explicitly; prioritize Claude/Antigravity only when authenticated and unpaused; otherwise choose healthy real workers without pretending |
| L12-R3-002: auto-worker process records go missing | activity log includes worker process missing reconciliation for several tasks, including closeout work | review/closeout can appear started but not finish | add worker outcome guard and retry policy that requeues or fails with bounded reason, not silent stale status |
| L12-R3-003: status planes regress | #4273 was repaired to `141d06ec...`, but `ai-status.json` regressed to `f6d340ff...` / `in_progress` | supervisor may dispatch owner lane instead of reviewer lane and overwrite exact-head truth | repair status mirror/source_ref sync and fail closed on stale source_ref regression |
| L12-R3-004: GitHub review bridge incomplete | #4269 has activity approval but GitHub latest reviews empty | branch protection remains blocked despite internal approval | ensure reviewer workers submit/bind GitHub reviews or produce an accepted governed alternative that branch policy recognizes |
| L12-R3-005: sidecar path not available | chair review denied sidecars because `underutilization_dispatch.enabled` is false and sidecar path has known regression | "maximize parallel fleets" cannot rely on sidecars right now | maximize primary-lane parallelism instead; only enable sidecars after bounded parent-support contracts and sidecar regression fix |
| L12-R3-006: live shared checkout is dirty | shared `/home/lupin/pantheon` has config/runtime/user/worker changes | direct edits risk overwriting active workers or config | all development must use clean task worktrees and PRs |

### R3 consequence

Fleet dispatch is not totally dead: supervisor is starting real Codex/Codex2
auto-workers and occasionally Claude CLI workers. But fleet completion is not
trustworthy until status-source regression, missing-process reconciliation, and
review bridge issues are fixed. These are first-class gaps, not operator noise.

## Consolidated missing development

The remaining work should be treated as a parallel recovery program:

1. Close exact-head PR gates for #4273, #4274, #4269, #4267, and #4193.
2. Fix supervisor status/source_ref regression so task rows cannot move
   backwards after a newer pushed PR head exists.
3. Fix or formalize the GitHub review bridge so internal reviewer approvals
   unblock real PR gates.
4. Repair fleet worker outcome tracking so missing processes become bounded
   retry/reopen events with clear task ownership.
5. Materialize the continuous worker manifest for the twelve loops.
6. Implement grouped loop verification lanes:
   - knowledge: Source / Distillation / Alpha
   - learning: Teaching / Agora / Imitation / Consultation
   - runtime: Deployment / Capital
   - observation: Telemetry / Reconciliation / Evolution / BFF
7. Add hosted truth and final closeout only after the preceding lanes are
   merged and deployed.

## Consolidated missing tests and validation

No completion claim is acceptable without:

- loop catalog and BFF loop-health tests proving no registry-only loop is
  presented as live;
- exact-head CI for each active PR after compose to current `dev`;
- GitHub or policy-recognized independent review for each PR;
- worker restart, retry, DLQ, and idempotency tests per loop family;
- service auth and tenant-boundary negative tests;
- no-live-capital and safe-write default tests for runtime/capital lanes;
- hosted FE/BFF smoke tests against Pantheon-owned dev URLs with strict BFF
  fallback;
- task-state/source-ref regression tests that simulate newer PR head followed
  by stale supervisor dispatch sync;
- final evidence archive with checksum and source PR/merge bindings.

## Dispatch policy for the follow-up packet

The execution tasks in
`docs/bff/execution-tasks/2026-07-27-twelve-loop-fleet-recovery/` are designed
to maximize real fleet parallelism:

- owner/reviewer are always distinct;
- work uses supervisor/auto-worker lanes, not Codex conversation subagents;
- tasks that only need PR review/closeout can run in parallel;
- grouped verification tasks wait only on their actual prerequisites;
- hosted and final closeout are intentionally last;
- Claude and Antigravity should be preferred when healthy, but the task packet
  must not stall forever if those lanes are paused/auth-down; healthy
supervisor-admitted workers may proceed with a recorded reason.

## Continuation audit addendum — 2026-07-27T21:30Z

After this packet was opened as PR #4277, the supervisor was used to dispatch
the packet to real auto-worker lanes. This addendum records the additional
evidence from that live run so the audit does not freeze at the earlier
snapshot.

### Dispatch and lane health

- The three new control-plane tasks were admitted to supervisor/auto-worker
  lanes, not conversation subagents.
- Antigravity remained unavailable because the live provider probe reported
  quota/auth failure (`local_cli_worker_supported=false`, `auth_ready=false`).
- Claude remained unavailable after the quota pause window because the live
  provider probe reported authentication/OAuth refresh failure.
- Codex/Codex2 were usable for already-started workers, but the provider
  watchdog repeatedly misclassified non-auth worker output and model-cache
  output as auth pauses. That behavior is now confirmed as a fleet-control
  bug, not an operator login task.

### Task and PR outcomes observed after dispatch

| Workstream | Live outcome | Updated gap |
|---|---|---|
| `L12-CURRENT-GAP-FLEET-AUDIT-20260727` / PR #4269 | Reviewer-approved exact head `5acc84f67972bbd3f63157250b50753c2199a35c`; merged to `dev` as `58f7ee46a95b55fc7a88bd399cd40e55350fbf73`; canonical task archived `done` | Closed point-in-time audit only. This does not prove twelve-loop operability. |
| `OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001` / PR #4273 | Reviewer approved exact head `5ce0b9a58924bb47f9c2b369fc30821411051e81`; finalize worker produced a newer head `f91ed836aba84bd86bfd64ce0b1ce187f96be7de`; PR became `BEHIND` after #4269 merged | Needs latest-`dev` compose, exact-head CI, and final closeout. |
| `L12-EVO-001` / PR #4267 | Independent reviewer rejected exact head `6a534392c81d0eca58d08f289f3a9e1dd033e992` | Real acceptance blocker: direct failed downstream receipt leaves decision `executed/failed`, no compensation, and pending outbox; root compose also defaults evolution auth to disabled/empty token, contradicting tenant-authority evidence. |
| `L12-DIST-001` / PR #4193 | Independent reviewer rejected exact head `62fecb4bb4c8f1fd55eb3ae014b7e6f746c91b50` | Real acceptance blocker: Registry idempotency accepts same-id StrategySpec with different payload/lineage as terminal success, violating source/draft lineage and terminal-write proof. |
| `L12-GITHUB-REVIEW-BRIDGE-001` | Worker implemented status-side GitHub review evidence plumbing, but also hit missing merge-gate file assumptions in its task worktree | Review bridge must be reconciled with the actual merge-gate code path on current `dev`; branch-protection proof remains incomplete. |
| `L12-FLEET-WORKER-OUTCOME-001` | Worker added missing-worker/retry outcome tests; the same live run then exposed repeated stale auth-pause classification for Codex workers | Outcome tracking must include provider-pause classification tests for Codex model-cache/usage/auth distinctions and avoid indefinite false auth pauses. |

### Addendum conclusion

The execution packet successfully maximized available real fleet parallelism:
multiple supervisor workers ran concurrently, and at least one audit task
completed through PR merge and archival. It also proved that the twelve-loop
program is still not complete. The remaining blockers are now more precise:

1. Evolution loop failed on real terminal failure compensation and default
   tenant-auth posture.
2. Distillation loop failed on Registry idempotency/lineage verification.
3. Telemetry closeout needs recompose after `dev` advanced.
4. GitHub review bridge is still incomplete for branch-protection proof.
5. Fleet provider health classification still produces false indefinite auth
   pauses.

Therefore the correct status is **fleet active, delivery incomplete, twelve
loops not yet operational**.
