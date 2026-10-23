# Hosted All-12-Loop Causal Chain: Design

Task: `HOSTED-ALL12-CHAIN-DESIGN-20261023` (design only; owner Claude, reviewer Antigravity2)
Status: **proposed — requires operator approval before any implementation task is opened**
Parent obligation: `DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002` acceptance item 2 ("Fresh Causal All-12"),
recorded as open in `docs/deployment/latest-dev-six-area-closure.md`.

This document changes no workflow, harness or service. Everything it calls "new" is an
implementation task listed in section 10.

## 1. Goal and non-goals

Goal: one fresh, labelled, paper-only causal chain that crosses the twelve canonical loops
(`source_ingestion` → `strategy_distillation` → `alpha_replication` → `persona_teaching` →
`agora_interaction_evidence` → `human_imitation_shadow_evaluation` → `consultation` →
`promotion_deployment` → `capital_pool_execution` → `telemetry_reconciliation` → `evolution` →
`bff_health_monitoring`) on the **persistent hosted dev stack**, with real human business approvals
and, per the original criterion, genuine Persona Pattern positives from real paper episodes.

Non-goals: no new deployment platform, evidence platform or dispatcher; no live broker or real
capital; no change to the isolated gate, which stays the regression proof for the harness itself;
no fabricated market events, approvals or episodes.

## 2. Where it runs (acceptance 2)

* Only as a new job in the existing `.github/workflows/nonprod-deploy.yml`, selected by a new
  dev-only `workflow_dispatch` input (`hosted_all12_chain`, mutually exclusive with
  `isolated_provider_qualification`, same protection: exact current `dev` SHA, `component=auto`,
  `allow_dirty=false`, strict auth). Same `concurrency` group
  `pantheon-nonprod-deploy-dev`, so it queues behind and never cancels other dev dispatches
  (`docs/conventions/GIT_WORKFLOW.md` §9.1, preference 1).
* Exclusivity for every *mutating* segment comes from `scripts/dev_environment_lease.py`, acquired
  with `--mode qualification` (already a valid mode), identity-bound heartbeat, released only after
  the segment, TTL self-expiry on crash. No lease extension is needed, because the run is CI-driven
  with `PANTHEON_ENVIRONMENT_LEASE_TOKEN` as a workflow secret.
* Local/ad hoc pytest against the `pantheon` project stays forbidden. Workers never run this.
* It reuses, unmodified in intent, `tests/integration/l12/test_current_research_loops_deployed_e2e.py`,
  `test_current_human_learning_deployed_e2e.py`, `test_current_runtime_loops_deployed_e2e.py` and the
  parent `test_stimulus_cross_loop_deployed_e2e.py`, through a hosted mode flag (section 5) plus the
  existing `l12_owner_auth.py` token seam. Evidence uses the existing atomic evidence-output contract
  and the existing `docs/deployment/evidence/<TASK>/` convention. Nothing else is added.

## 3. Segments, pause and resume (acceptance 5)

Human approvals take hours; a lease must not be held across them. The run is therefore split into
segments, each a separate dispatch of the same workflow with `chain_id` and `chain_phase` inputs.
The chain's state is not stored in a new store: the next segment reads the labelled records back
from the owners over HTTP (every record carries `chain_id`, section 4) and refuses to proceed if an
expected predecessor record or approval is absent. Replays are idempotent by owner idempotency keys
derived from `chain_id` + step.

| Phase | Lease | Loops | Ends at |
|---|---|---|---|
| `A_research` | held | 1–4 (reuse research suite) | Teaching result recorded |
| `B_learning` | held | 5–7 (human-learning suite) | Consultation memo + governance handoff; **pauses for H1/H2** |
| `C_promote` | held | 8–9 (runtime suite, hosted subset) | Paper pool, binding, plan, runtime binding active; **pauses for H3/H4 as needed** |
| `D_observe` | none (read-only) | 9–12 | Repeated on later trading days; collects episodes, telemetry, drift, evolution, BFF health |
| `E_close` | held briefly | 11–12 + stimulus closure parent | Evidence sealed, retirement run |

A paused segment writes nothing and waits for the next dispatch, so there is no timeout-driven
auto-approval path. The workflow never calls approve/decide endpoints on a human's behalf.

### Human approval points

The harness-minted `PANTHEON_L12_OPERATOR_TOKEN` / `..._REVIEWER_TOKEN` (`l12_owner_auth.py`) are
**rejected in hosted mode**; hosted mode only accepts that a decided approval already exists, made
through the Management UI.

| ID | Approval | Maker | Checker | Blocks |
|---|---|---|---|---|
| H1 | Registry artifact approval for the chain's strategy artifact (Governance propose → review → decide, bound to the exact `registry_id` + candidate digest) | operator (human 1) | governance_reviewer (human 2) | `C_promote` |
| H2 | Agora synthesis consent (two existing Persona opinions + one synthesis; the previously pending request must not be reused for this chain) | operator | — (consent by the data owner; recorded, not auto-created) | consultation/synthesis in `B_learning` |
| H3 | Deployment plan release for the labelled paper scope | operator | governance_reviewer | runtime binding |
| H4 | Paper capital binding approval (`approval_decision_id`, paper context only) | operator | governance_reviewer | capital loop |
| H5 | Evolution decision acceptance | operator | governance_reviewer | closure of loop 11 |

Maker and checker must be different authenticated subjects; the segment aborts if the decided
approval has the same subject for both. No extra gates (MFA, issuer, execution grants) are invented,
consistent with the operator's earlier withdrawal; scope, tenant, secrets and paper/live separation
stay as they are.

## 4. Owner writes, labels and retirement (acceptance 4)

Label contract for every record: tenant = the hosted dev tenant (never `*`), id prefix
`hosted-all12-<chain_id>-`, and metadata/labels `{"chain_id", "task_id":
"HOSTED-ALL12-CHAIN-DESIGN-20261023", "paper_only": true, "execution_context": "paper",
"fixture": "hosted_all12"}`. A record whose owner cannot carry metadata is identified by id prefix and
listed in the evidence manifest. Retirement uses each owner's existing retire/archive/revoke/close
operation; section 10 task T1 must confirm the exact endpoint per owner, and where an owner has no
retirement operation the record is left labelled and reported as residual — never deleted from the
database by hand.

| Owner write | Created in | Retirement / cleanup |
|---|---|---|
| Source/strategy/experiment/teaching records (loops 1–4) | A | Read-only history; labelled, not retired. Excluded from other tasks by `chain_id`. |
| Agora interaction evidence + dataset version (loop 5) | B | Labelled; consent record (H2) expires on its own finite window. |
| Imitation candidate / shadow evaluation (loop 6) | B | Labelled; closed by its own terminal state. |
| Consultation + governance handoff (loop 7) | B | Closed memo; handoff acknowledged. |
| **Capital pool** (paper) | C | Close/deactivate pool in `E_close`. |
| **Persona capital binding** | C | Revoke/deactivate in `E_close`; must be done before the pool closes. |
| **Registry artifact approval** (mutation of a chain-owned child of `artifact-tw-session-momentum-v1`, never the parent) | C | Revoke approval / deprecate chain artifact in `E_close`. |
| **Deployment plan** | C | Terminal plan state, then superseded/cancelled by the owner. |
| **Runtime binding** | C | Deactivate in `E_close`; confirm fleet-ready count returns to the pre-run baseline. |
| Telemetry/reconciliation/drift/incident (loops 10) | D | Close incident; labelled history retained. |
| Evolution decision (loop 11) | E | Terminal after H5; labelled. |

`E_close` ends with a readback proving: baseline fleet-ready/binding counts restored, zero active
chain-labelled bindings or pools, and no non-chain record changed. Live broker and real capital are
unreachable by construction: the paper context is required by the owners, and the segment asserts
`paper_only` and the absence of any live execution grant before every write.

## 5. Cases excluded or replaced on hosted (acceptance 3)

No segment stops, restarts, recreates or reconfigures a shared service.

| Case / step | Source | Hosted treatment | Reason |
|---|---|---|---|
| `negative_typed_worker_failure` (`docker compose stop/start paper-fleet-reconciler`) | runtime suite | **Excluded**; replaced by read-only BFF typed-worker/API health observation (loop 12) | Stops a shared worker (README finding 3). Failure-discrimination stays proven by the isolated gate. |
| `_restart_service` (`docker compose restart`) in the human-learning suite | human-learning suite | **Excluded**; replaced by read-only service identity (`started_at` unchanged) plus an idempotent same-key replay that must not create a second durable result | Restarts shared workers. |
| Parent stimulus gate "restarts the isolated runtime worker" | stimulus suite | Restart step skipped in hosted mode | Same. |
| `invalid_bindings_migration_prerequisite` / harness pre-flight retirement of pre-existing invalid bindings | runtime suite, `run_isolated_l12_runtime_e2e.py` | **Excluded**; replaced by read-only check that pre-existing invalid bindings are reported, not altered | Mutates records the chain does not own. |
| Missing-checksum and missing-market-symbol negatives | runtime suite | **Excluded**; keep isolated proof | They create deliberately broken bindings that shared fleet-readiness counters would see. |
| Harness pre-seeded market price snapshot | `run_isolated_l12_runtime_e2e.py` | **Not used**; the chain reads the real stored snapshots only | Seeded prices would fabricate market data (section 6). |
| `bounded_lifecycle_cursor_and_resource_limits` | runtime suite | Keep only if T2 confirms it is read-only; otherwise excluded | Not yet verified in this design pass. |
| `docker compose` identity lookups (`ps`, `inspect`) | all suites | Allowed (read-only); project name comes from the workflow, not the suites' `pantheon` default | Observation only. |

## 6. Genuine Persona Pattern positives (acceptance 6)

The original criterion requires at least two uncovered, naturally closed episodes, then natural
reflection, BFF reload and next-interval dedupe. The design does not manufacture these:

1. The chain's labelled persona is bound to the labelled paper pool and runs through the normal
   paper runtime on the stored real market data from `source-ingest`. No prices, fills or episodes
   are injected, backdated or seeded.
2. An episode is "naturally closed" only when the persona's own strategy exit rule fires on real
   data during a TW trading session (09:00–13:30 Asia/Taipei, Mon–Fri, excluding exchange holidays).
   The `D_observe` phase is therefore dispatched on trading days and records per-day episode counts,
   with timestamps in Asia/Taipei and the source snapshot ids that drove each entry/exit.
3. "Uncovered" means no existing Pattern record already covers the episode; the check is a readback
   from the Persona/Pattern owner, not a local assumption. Episodes carry the chain label so they
   cannot be confused with the two existing Patterns (`72215b2f-…`, `7ce68816-…`).
4. Reflection is the scheduler's own run; the harness only polls its HTTP-visible effect (same
   technique as the Loop 5 drain in the human-learning suite). Then BFF reload shows the Pattern, and
   a second scheduler interval must produce no duplicate.
5. Honest outcomes: if the observation window (default 5 trading days, operator-adjustable before
   approval) yields fewer than two closed uncovered episodes, the result is recorded as
   "Pattern positive not observed", with the episode counts. Negative Sharpe, Teaching rejection,
   quarantine and dedupe are valid outcomes, not failures, and are not rewritten as approvals.
   Acceptance item 2 stays open in that case; no episode is fabricated to close it.

## 7. Resolution of the 2026-08-17 runtime-loops README findings

| README finding | Resolution |
|---|---|
| Single persistent `pantheon` project of 51 shared services | The chain only adds labelled records through public owner APIs; it touches no service lifecycle (section 5). |
| Every case writes real CapitalPool/Binding/registry/plan/runtime state | Each write is labelled, tenant-scoped, paper-only and listed with its retirement path (section 4); baseline restored and verified in `E_close`. |
| `negative_typed_worker_failure` stops `paper-fleet-reconciler` | Excluded, replaced by read-only observation (section 5). |
| §9.1: exclusivity must use the governed lease, not a home-rolled action | All mutating segments hold the dev environment lease inside `nonprod-deploy.yml`; queuing relies on the workflow concurrency group. |
| Worker environment has no `PANTHEON_ENVIRONMENT_LEASE_TOKEN` | The run executes in the CI job where the token is a workflow secret; local workers still do not run it. |
| Lease covers only the CI deploy job, not ad hoc proof runs | Satisfied by adding the proof as a job of that same workflow with lease mode `qualification`; no lease change and no ad hoc path is created. |
| No way to bound the outage window to the suite's own traffic | There is no outage: no shared service is stopped or restarted. |

## 8. Risks

* Owner retirement operations may be missing; mitigated by labels, residual reporting and T1.
* Real markets may yield no episodes in the window (section 6, point 5).
* Humans may not be available; the chain stays parked, costs nothing, holds no lease.
* Mutating segments queue behind other dev dispatches; acceptable.
* Hosted mode must not weaken the isolated gate; T2 keeps hosted behaviour behind an explicit flag and tests default behaviour unchanged.

## 9. Operator decisions requested before implementation

1. Approve this design and the five approval points, and name the two human subjects for maker/checker.
2. Confirm the observation window (default 5 TW trading days).
3. Confirm that cases in section 5 are acceptable as excluded, with isolated-gate evidence for them.

## 10. Implementation tasks (opened only after approval)

| ID | Scope | Files (indicative) | Acceptance |
|---|---|---|---|
| T1 Owner retirement and label audit | Confirm per-owner write/retire endpoints and label support for the section 4 records | docs only: `docs/deployment/hosted-all12-causal-chain-design.md` appendix; no code unless an owner lacks retirement (then a separate owner task) | Table of endpoint per owner; residuals listed |
| T2 Hosted mode in L12 suites | `PANTHEON_L12_HOSTED_CHAIN=1` flag: label injection, human-approval wait/readback, skip list from section 5, reject minted tokens | `tests/integration/l12/l12_owner_auth.py`, the three domain suites, stimulus suite, matching unit tests | Default behaviour unchanged (existing tests green); hosted flag skips each section 5 case; no `docker compose stop/restart` reachable in hosted mode (static test) |
| T3 Hosted chain driver | Segment runner reading chain state from owners, idempotency keys, `E_close` retirement and baseline check | `scripts/run_hosted_all12_chain.py`, `tests/unit/scripts/test_run_hosted_all12_chain.py` | Replays do not duplicate; missing approval pauses; same-subject maker/checker aborts |
| T4 Workflow job | New job + inputs in the existing workflow with lease `qualification`, concurrency group, exact-dev guard | `.github/workflows/nonprod-deploy.yml`, workflow guard tests | Lease acquired/released per segment; no new dispatcher; inventory/pinning checks pass |
| T5 Pattern observation | `D_observe` readbacks (episodes, uncovered check, scheduler reflection, BFF reload, dedupe) | driver + persona-owner read client in `scripts/run_hosted_all12_chain.py` | Records episode counts with Asia/Taipei timestamps; never writes market data or episodes |
| T6 Operator runbook | Management UI approval steps and dispatch commands | `docs/deployment/hosted-all12-causal-chain-runbook.md` | Operator can run H1–H5 without engineering help |
| T7 Hosted run and closeout | Execute phases A–E with the operator and humans; seal evidence; update the closure doc | `docs/deployment/evidence/HOSTED-ALL12-CHAIN-<DATE>/`, `docs/deployment/latest-dev-six-area-closure.md` | All twelve loops linked by one `chain_id`, or an honest not-observed record; cleanup verified |

Order: T1 → T2 ∥ T3 → T4 → T5 → T6 → T7. Each task is its own PR with an independent reviewer.
