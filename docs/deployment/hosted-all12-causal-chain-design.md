# Hosted All-12-Loop Causal Chain: Design

Task: `HOSTED-ALL12-CHAIN-DESIGN-20261023` (design only; owner Claude, reviewer Antigravity2)
Status: **design approved by the operator on 2026-10-23 (PR6736, merge `43473d151`); T1 audit appendix added by `HOSTED-ALL12-OWNER-AUDIT-20261023`. The H1–H4 business decisions are not approved by design consent.**
Parent obligation: `DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002` original parent acceptance item 3
(Hosted Harness, Twelve-Loop Causal Chain & Journey Verification; "Fresh Causal All-12"), recorded as
open in `docs/deployment/latest-dev-six-area-closure.md` (Area 3, Remaining Obligation 1). The parent's
original 6 acceptance criteria, 21 dependencies, 3 formal files and 49 criteria are unchanged by this
document, and the sealed Research, Capital, Persona and FE checkpoints are not reopened.

This document changes no workflow, harness or service. Everything it calls "new" is a **proposal**
(section 10) that stays unapproved until the operator approves it. Section 5A first inventories what
existing callers and APIs already do, so any proposal is limited to the concrete gaps found there.

## 0. Authority correction (operator decision, 2026-10-23)

The operator withdrew the earlier extra "two additional real humans" rule and authorised recording
their decisions under their real execution identity. This design therefore:

* requires **no second human** for any approval point and has no "same subject fails automatically"
  rule; no fake account, MFA, issuer or execution-grant gate is introduced;
* keeps the real authorisation and identity audit with two separately recorded facts: (a) the **real
  decision-maker** and the conversation consent evidence for the decision, and (b) the **actual
  executing actor/session** that saved it through the existing owner API (which may be an authorised
  executor acting on the human's decision). The executor never impersonates a human login, never asks
  the user to log in a test account, and never fabricates MFA or new tokens. The evidence also records
  tenant, owner version and timestamp read back from the owner. An unapproved business decision still
  needs consent in its original scope; saving an already-authorised true decision is not blocked by a
  UI-only rule.

The task's historical acceptance item 5 still contains the words "maker-checker separation". The
native CLI has no acceptance-edit entry, so the canonical task JSON is **not** changed and this
document does not claim it was. Where they differ, this correction governs (higher-priority user
decision).

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
* Known shared-stack limit, not worked around: the existing runtime suite requires the whole fleet to
  have exactly one running worker (`test_current_runtime_loops_deployed_e2e.py` ~L1708). That cannot
  hold on the persistent shared fleet, so assertions that depend on it are replaced by the
  chain-scoped readbacks of section 5A, never by touching the fleet.
* The paper capital pool and paper persona binding need no live approval
  (`services/capital/capital_guard.py:is_paper_operation`; sealed
  `CAPITAL-FORWARD-HOSTED-ACCEPTANCE-001` AC3: "without a Capital approval or invented policy
  reference").

## 3. Segments, pause and resume (acceptance 5)

Human approvals take hours; a lease must not be held across them. The run is therefore split into
segments, each a separate dispatch of the same workflow with `chain_id` and `chain_phase` inputs.
The chain's state is not stored in a new store: the next segment reads the labelled records back
from the owners over HTTP (every record carries `chain_id`, section 4) and refuses to proceed if an
expected predecessor record or approval is absent. Replays are idempotent by owner idempotency keys
derived from `chain_id` + step.

| Phase | Lease | Loops | Ends at |
|---|---|---|---|
| `A_research` | held | 1–4 (reuse research suite) | Segment A1 (loops 1–2) ends with the distilled draft StrategySpec read back; it pauses before the Registry approval prerequisite until H1 is decided for that exact `registry_id` + checksum. Segment A2 (loops 3–4) starts only after H1 and ends with the Teaching result recorded |
| `B_learning` | held | 5–7 (human-learning suite) | Consultation memo + governance handoff, started only once H2 is decided (otherwise stops before consultation/synthesis); H1 was already decided in `A_research` |
| `C_promote` | held | 8–9 (runtime suite, hosted subset) | Paper pool and paper binding (no approval needed), plan, runtime binding active; the runtime binding is created only after H3, otherwise stops before that action |
| `D_observe` | none (read-only) | 9–12 | Repeated on later trading days; collects episodes, telemetry, drift, evolution, BFF health |
| `E_close` | held briefly | 11–12 + stimulus closure parent | Evidence sealed, retirement run |

Ordering rule: an approval or consent is obtained *before* the outbound action or owner mutation it
covers (the "Blocks" column below), never after. If it is not yet decided, the segment stops before
that action, releases the lease, and a later dispatch resumes; the decision itself is still made only
by the originally designated humans (saving that decision through an authorised executor is allowed, section 0). Ordinary read-only calls need no approval. This adds no approval
point beyond H1–H4.

A paused segment writes nothing and waits for the next dispatch, so there is no timeout-driven
auto-approval path. The workflow never invents or infers a decision; it calls approve/decide endpoints
only to record a decision a real human has actually made and authorised for saving, never otherwise.

### Human approval points

Each point is decided by a real human (decision source); it may be saved either by that human in the
Management UI or by an authorised executor through the existing owner API, recording decision-maker
and executing actor/session separately (section 0). One authorised human per point is sufficient.

The harness-minted `PANTHEON_L12_OPERATOR_TOKEN` / `..._REVIEWER_TOKEN` (`l12_owner_auth.py`) are
**rejected in hosted mode**; hosted mode only accepts that a decided approval already exists, saved
under a real executing identity with the real decision-maker recorded (section 0).

| ID | Approval | Decided by | Blocks |
|---|---|---|---|
| H1 | Registry artifact approval for the chain's distilled StrategySpec (Governance propose → review → decide, bound to the exact `registry_id` + checksum read back from loop 2). It is the existing approval prerequisite the research suite already crosses before Alpha admission (`distill.reviewed_admission_prerequisite`), so it is paused and read back **before** Alpha admission, not before `C_promote`. The same approved `registry_id` is what Alpha admission (`strategy_spec_id`) and later `C_promote` consume; a later approval of a different artifact does not satisfy H1, and no second H1 is created. A Registry/Governance approval, **not** a Capital approval, and never described as one. | authorised human (UI, or saved by authorised executor) | Alpha admission (loop 3) in `A_research`; therefore also `C_promote` |
| H2 | Agora synthesis consent (two existing Persona opinions + one synthesis; the previously pending request must not be reused for this chain) | data owner; recorded, not auto-created | consultation/synthesis in `B_learning` |
| H3 | Deployment plan release for the labelled paper scope | authorised human (UI, or saved by authorised executor) | runtime binding |
| H4 | Evolution decision acceptance | authorised human (UI, or saved by authorised executor) | closure of loop 11 |

There is no paper Capital approval: the paper pool and paper persona binding are created under the
paper operation policy with no live approval. No extra gates (MFA, issuer, execution grants) are
invented; scope, tenant, secrets and paper/live separation stay as they are.

### Capital write identity (decision required)

Hosted mode mints no service token anywhere: neither the workflow nor `l12_owner_auth.py` calls
`_service_jwt('capital')`. Capital writes (`POST /api/capital-pools`, `/api/bindings`, `/activate`)
send the executing session's own token as received. Capital binds a mutation to the verified token
(`services/capital/inbound_authority.py`): `X-Pantheon-Service` must be one of the token's service
claims and be in `CAPITAL_ALLOWED_CALLER_SERVICES`, and the body actor must equal the token's
delegated actor or subject. A human session token carries no service claim, and the existing dev
paper principals (`docs/deployment/dev-paper-principals-20260908.md`) cover Governance only. So when
the executor token has no service claim, `C_promote` pauses before `capital_pool_creation` with
blocker `CAPITAL_HOSTED_PRINCIPAL_DECISION_REQUIRED`; no Capital write is attempted.

Decision needed from the owner (no new identity mechanism is added here): either (a) Capital accepts
the executing human session for paper pool/binding writes (recording that session as actor), or
(b) a dev paper principal is authorised for Capital with the same tenant-dev/paper/0 scope as the
Governance one. Until one is chosen, the hosted chain stops at this blocker.

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
| **Capital pool** (paper; no Capital approval) | C | Close/deactivate pool in `E_close`. |
| **Persona capital binding** (paper; no Capital approval) | C | Revoke/deactivate in `E_close`; must be done before the pool closes. |
| **Registry artifact approval** (H1; the chain-owned distilled StrategySpec entry, never a pre-existing parent such as `artifact-tw-session-momentum-v1`) | A | Revoke approval / deprecate chain artifact in `E_close`. |
| **Deployment plan** | C | Terminal plan state, then superseded/cancelled by the owner. |
| **Runtime binding** | C | Deactivate in `E_close`; confirm fleet-ready count returns to the pre-run baseline. |
| Telemetry/reconciliation/drift/incident (loops 10) | D | Close incident; labelled history retained. |
| Evolution decision (loop 11) | E | Terminal after H4; labelled. |

`E_close` ends with a readback proving: baseline fleet-ready/binding counts restored, zero active
chain-labelled bindings or pools, and no non-chain record changed. Live broker and real capital are
unreachable by construction: the paper context is required by the owners, and the segment asserts
`paper_only` and the absence of any live execution grant before every write.

## 5. Cases excluded or replaced on hosted (acceptance 3)

No segment stops, restarts, recreates or reconfigures a shared service.

| Case / step | Source | Hosted treatment | Reason |
|---|---|---|---|
| `negative_typed_worker_failure` (`docker compose stop/start paper-fleet-reconciler`) | runtime suite | **Excluded**; replaced by read-only BFF typed-worker/API health observation (loop 12) | Stops a shared worker (README finding 3). Failure-discrimination stays proven by the isolated gate. |
| `_restart_service` (`docker compose restart`) in the human-learning suite | human-learning suite | **Excluded**; the chain triggers no restart. `started_at` unchanged plus a same-key replay shows idempotency only and is **not** the original parent AC4 cross-process restart proof. Restart persistence is claimed only if the chain's labelled records are read back before **and** after a restart that happens anyway (a coordinated release/version change or a genuine owner restart), with the same resource IDs/versions compared and the owner process identity shown to differ. If no such event occurs during the run, restart persistence is recorded as **not completed**. | The chain must not restart shared workers; the shared stack may restart for its own reasons. |
| Parent stimulus gate "restarts the isolated runtime worker" | stimulus suite | Restart step skipped in hosted mode | Same. |
| `invalid_bindings_migration_prerequisite` / harness pre-flight retirement of pre-existing invalid bindings | runtime suite, `run_isolated_l12_runtime_e2e.py` | **Excluded**; replaced by read-only check that pre-existing invalid bindings are reported, not altered | Mutates records the chain does not own. |
| Missing-checksum and missing-market-symbol negatives | runtime suite | **Excluded**; keep isolated proof | They create deliberately broken bindings that shared fleet-readiness counters would see. |
| Harness pre-seeded market price snapshot | `run_isolated_l12_runtime_e2e.py` | **Not used**; the chain reads the real stored snapshots only | Seeded prices would fabricate market data (section 6). |
| `bounded_lifecycle_cursor_and_resource_limits` | runtime suite | Keep only if T2 confirms it is read-only; otherwise excluded | Not yet verified in this design pass. |
| `docker compose` identity lookups (`ps`, `inspect`) | all suites | Allowed (read-only); project name comes from the workflow, not the suites' `pantheon` default | Observation only. |

## 5A. Existing capability inventory and causal-edge gaps

**What exists (read from the suites, not run).**
`test_stimulus_cross_loop_deployed_e2e.py` starts the three domain suites as separate child processes
sharing only a `PANTHEON_L12_CROSS_LOOP_RUN_ID` string, then normalises each loop's
`trigger / terminal_output / authority_readback / next_consumer` and reads Management loop health. The
research suite's own cases chain by real IDs inside that suite (Source → Distillation → Registry
approval → Alpha admission → Teaching). The human-learning and runtime suites each create their own
records. Existing callers can therefore already: run each domain against a deployed stack, emit
evidence, and read owner state over HTTP. They cannot yet: skip the shared-infrastructure cases in
section 5 on a stack that is not isolated, wait on a human decision, or pass one domain's output ID
into the next domain. These three are the only concrete gaps T2/T3 would cover.

**Why a shared `chain_id` and three domain receipts are not enough.** A shared string and three passing
domain reports show the suites ran in the same window, not that each loop consumed the previous
loop's output. Causality needs, for every edge, the predecessor's output ID and a consumer record that
references that exact ID. Status of each edge in the current suites:

| Edge | Predecessor output ID | Next consumer reference | Gap |
|---|---|---|---|
| 1→2 | `SourceRecord.id` + digest | Distillation receipt `distillation_job_id` with `metadata.distillation.source_id/source_digest` equal to it | none inside research suite |
| 2→3 | Registry approved entry `registry_id` | `ReplicationAdmission.admission_id` with `strategy_spec_id == registry_id` | none inside research suite |
| 3→4 | Alpha `experiment_run_id` | `TeachingSession` `session_id` persisting the Alpha authority/domain refs | none inside research suite |
| 4→5 | Teaching evaluation / `session_id` (`TeachingTerminalReadback`) | Agora interaction evidence input | **gap**: human suite builds its own interaction; no reference to the Teaching ID found |
| 5→6 | `dataset_version_id`, `handoff_id` | `ShadowImitationCandidate.candidate_id` | within human suite only; not tied to loop 4 |
| 6→7 | `candidate_id` / `ExperimentRun` id | `ConsultRequest` id → memo | within human suite only; not tied to loop 6 of the same chain |
| 7→8 | `ConsultGateHandoff.handoff_id` | deployment plan referencing it | **gap**: runtime suite creates its own artifact/plan; no reference to the handoff |
| 8→9 | plan id | runtime binding / capital binding `binding_id` | within runtime suite |
| 9→10 | `binding_id`, `paper_fill_simulated` event id | telemetry drift id (`drift-…`), incident `linked_incident_id` | within runtime suite |
| 10→11 | incident / drift id | evolution decision referencing it | within runtime suite |
| 11→12 | none required: loop 11 is `EvolutionDecision` (threshold + daily sweep); loop 12 is a separate continuous/event path | Per `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md` §3.12: BFF health metrics → telemetry ingest → incident pipeline; no EvolutionDecision precedes it | **not an edge**: report loop 12's own caller (continuous probe / error-rate event), output (health metrics) and consumer (telemetry ingest → incident pipeline); fresh evidence if observed, otherwise "not observed" |

The two **gap** edges (4→5, 7→8) plus the cross-suite seams are what a "same fresh chain"
claim actually lacks today. The twelve loops follow the hybrid trigger topology of the loop policy, not
a forced linear order: loop 12 is not chained from loop 11, and no new field or gate is added for it. The run must record, per edge, the predecessor ID and the consumer
record that carries it, and any edge where the consumer does not carry it is reported **unproven**,
not inferred from the shared `chain_id`. T1 verifies this table against the live owner payloads before
anything is built.

## 6. Genuine Persona Pattern positives (acceptance 6)

The original six Pattern conditions (any legitimate dev Persona with at least two uncovered, naturally
closed episodes; natural reflection at the scheduler interval; BFF reload; dedupe at the immediately
following interval) apply as written. This design adds no parent acceptance gate and narrows no scope.
The chain's labelled Persona is an **example** source of episodes, not a required one: any legitimate
dev Persona meeting the original conditions counts, and the result names which one.

1. Episodes come from the normal paper runtime on stored real market data from `source-ingest`. No
   prices, fills or episodes are injected, backdated or seeded. Chain-labelled episodes carry the
   chain label; other Personas' episodes are identified by their own owner IDs.
2. "Naturally closed" means the Persona's own exit rule fired on real data in the normal paper runtime.
   No clock-time or weekday cutoff is imposed (the original condition and the owner's
   `qualifying_episodes` have none; an existing legitimate closed episode closed at 20:46 Taipei).
   `D_observe` records closed-episode counts with Asia/Taipei timestamps and the source snapshot ids
   behind each entry/exit.
3. "Uncovered" means no existing Pattern record covers the episode, checked by readback from the
   Persona/Pattern owner. The two existing Patterns (`72215b2f-…`, `7ce68816-…`) are not reused.
4. Reflection is the scheduler's own natural interval run; the harness only polls its HTTP-visible
   effect. Then BFF reload must show the Pattern, and the immediately following interval must produce
   no duplicate.
5. A 5-trading-day observation window is an optional recommendation, not a gate. If the conditions are
   not met when the operator ends observation, the result is "Pattern positive not observed" with
   counts. Negative Sharpe, Teaching rejection, quarantine and dedupe are valid outcomes, not
   approvals. The parent item then stays open; no episode is fabricated.

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
* No coordinated release or owner restart may occur during the run, leaving restart persistence uncompleted (section 5).
* Humans may not be available; the chain stays parked, costs nothing, holds no lease.
* Mutating segments queue behind other dev dispatches; acceptable.
* Hosted mode must not weaken the isolated gate; T2 keeps hosted behaviour behind an explicit flag and tests default behaviour unchanged.

## 9. Operator decisions requested before implementation

1. Approve this design and the four approval points (H1–H4). One authorised human each is sufficient; the real decision-maker and the executing actor/session are recorded separately.
2. Optionally confirm an observation window (5 TW trading days is a recommendation only).
3. Confirm that cases in section 5 are acceptable as excluded, with isolated-gate evidence for them.

## 10. Proposed implementation tasks (not approved; opened only after operator approval)

These are proposals, bounded by the gaps in section 5A. The new hosted flag and driver below exist only
if T1 shows that existing callers cannot do the job; the existing suites and workflow stay the default.
No task is started by this document.

| ID | Scope | Files (indicative) | Acceptance |
|---|---|---|---|
| T1 Inventory, edge and retirement audit | Confirm per-owner write/retire endpoints and label support (section 4), and verify the section 5A edge table against live payloads | docs only: `docs/deployment/hosted-all12-causal-chain-design.md` appendix; no code unless an owner lacks retirement (then a separate owner task) | Table of endpoint per owner; residuals listed |
| T2 Hosted mode in L12 suites | `PANTHEON_L12_HOSTED_CHAIN=1` flag: label injection, human-approval wait/readback, skip list from section 5, reject minted tokens | `tests/integration/l12/l12_owner_auth.py`, the three domain suites, stimulus suite, matching unit tests | Default behaviour unchanged (existing tests green); hosted flag skips each section 5 case; no `docker compose stop/restart` reachable in hosted mode (static test) |
| T3 Hosted chain driver | Segment runner reading chain state from owners, idempotency keys, `E_close` retirement and baseline check | `scripts/run_hosted_all12_chain.py`, `tests/unit/scripts/test_run_hosted_all12_chain.py` | Replays do not duplicate; missing approval pauses and nothing is approved on a human's behalf |
| T4 Workflow job | New job + inputs in the existing workflow with lease `qualification`, concurrency group, exact-dev guard | `.github/workflows/nonprod-deploy.yml`, workflow guard tests | Lease acquired/released per segment; no new dispatcher; inventory/pinning checks pass |
| T5 Pattern observation | `D_observe` readbacks (episodes, uncovered check, scheduler reflection, BFF reload, dedupe) | driver + persona-owner read client in `scripts/run_hosted_all12_chain.py` | Records episode counts with Asia/Taipei timestamps; never writes market data or episodes |
| T6 Operator runbook | approval-recording steps (UI or authorised executor) and dispatch commands | `docs/deployment/hosted-all12-causal-chain-runbook.md` | Operator can run H1–H4 without engineering help |
| T7 Hosted run and closeout | Execute phases A–E with the operator and humans; seal evidence; update the closure doc | `docs/deployment/evidence/HOSTED-ALL12-CHAIN-<DATE>/`, `docs/deployment/latest-dev-six-area-closure.md` | Every real causal edge linked by IDs and loop 12 shown via its own trigger path, or an honest not-observed record; cleanup verified |

Order: T1 → T2 ∥ T3 → T4 → T5 → T6 → T7. Each task is its own PR with an independent reviewer.

## Appendix A. T1 owner API and causal-seam audit (`HOSTED-ALL12-OWNER-AUDIT-20261023`)

Read-only source audit on `dev` at `854f33282`. No code, workflow, VM, provider refresh, owner write or
secret access. Machine-readable copy: `docs/deployment/evidence/HOSTED-ALL12-OWNER-AUDIT-20261023/evidence.json`.

### A.0 Operator design approval (actual)

The operator's real answer (conversation `01a11b73-51ee-7b52-bac2-bf1561292e00`, recorded
2026-10-23T13:48:32Z in `/tmp/pantheon-codex-01a11-review-20261023/hosted-all12-design-actual-user-approval-20261023.json`):
"批准設計，交 supervisor 按依賴執行" for PR6736 (reviewed head `d39f3cfc1`, merge `43473d151`).
Scope: implement through the existing supervisor in dependency order; new flag/driver only if T1 proves
existing callers insufficient. It does **not** approve any concrete H1–H4 decision. Parent
`DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002` is unchanged (6 AC, 21 deps, 3 files, 49 criteria).

### A.1 Owner write and retirement endpoint inventory (section 4)

"Tenant" = tenant taken from the authenticated identity or request; "idem" = idempotency key support.
Line numbers are `path:line` of the route decorator.

| Owner write | Create endpoint | Tenant / label / idem | Retirement operation | Residual |
|---|---|---|---|---|
| Source connector / record (loops 1) | `POST /api/source-ingest/connectors` `services/source_ingestion/routers/ingest_operations.py:168`; `POST …/source-records` `:281`; `POST …/jobs` `:258` | tenant via `connector.metadata.tenant_id` (`:73`, `:104`); labels via metadata | `PUT /api/source-ingest/connectors/{id}/lifecycle` `:235` (connector only) | records have no delete/retire: labelled history; no idem header |
| Distilled StrategySpec (loop 2) | `POST /api/registry/strategy-specs` `services/registry/service.py:1422` | `owner_tenant` checked (deployment `service.py:407`); metadata | `POST /api/registry/strategy-specs/{id}/advance` `:1540` → `retired` (`services/registry/models.py:68-71`, APPROVED→RETIRED allowed) | none |
| H1 Registry approval | `POST /api/governance/approvals` `services/governance/main.py:1468`, `/review` `:1564`, `/decide` `:1576` | tenant from identity; `Idempotency-Key` required (`:1370`) | `POST /api/governance/approvals/{id}/revoke` `:1588` | none |
| Alpha admission / run (loop 3) | `POST /api/research-orchestrator/alpha-replication/admissions` `services/research/main.py:941` | tenant in path of read `:956` | `POST /api/research-orchestrator/runs/{id}/cancel` `:1652` (runs only) | admission has no retire: labelled history |
| Teaching session (loop 4) | `POST /api/training/sessions` `services/training-session/main.py:760` | tenant via `_tenant_id_for` (`:107`); idem header on commit/discard | `POST /api/training/replays/{id}/discard` `:2903` | completed sessions are history |
| Agora interaction evidence (loop 5) | `POST /bff/agora/interaction-evidence` `services/control-plane/bff/agora/dataset_extraction/router.py:226` | `X-Tenant-Id`; `Idempotency-Key` required; `source_refs`, `session_id` free fields (`models.py:78-80`) | none; consent window `retention_days` | labelled history |
| Imitation candidate (loop 6) | `POST /api/policy-learning/agora-handoff` `services/policy-learning/main.py:984` (drainer also exists) | tenant bound (`bind_tenant`) | `POST /api/policy-learning/jobs/{id}/reject` `:551` (jobs only) | candidate ends at its own terminal state |
| Consultation (loop 7) | `POST /api/consult/requests` `services/consultation/main.py:277`, `/submit` `:345`, memos `:632`, handoffs `:856` | `tenant_id`, idempotency on every create (`:162-170`, `:640`, `:883`); `metadata` free | `POST …/requests/{id}/cancel` `:376` (409 when `linked_session_id`) | closed memo/handoff are history |
| H2 Agora synthesis consent | recorded by data owner (not API-created) | — | finite window | none |
| Capital pool (paper) | `POST /api/capital-pools` `services/capital/main.py:1081` | `tenant_id`, `idempotency_key` in body (`models.py:59-60`) | `PATCH /api/capital-pools/{id}/status` `:1109` → `archived` (`PoolStatus`, `governance/capital_pool.py:53`) | status PATCH has no idem key |
| Persona capital binding (paper) | `POST /api/bindings` `:1153`, `/activate` `:1188` | `tenant_id`, `idempotency_key` (`models.py:86-87`); metadata | `PATCH /api/bindings/{id}/status` `:1198` → `revoked` | revoke before archiving pool |
| Deployment plan | `POST /api/deployment/plans` `services/deployment/service.py:2211` | tenant forced from identity (`:420-427`); `metadata` free; no idem header (plan_id caller-chosen) | `POST …/plans/{id}/status` `:2323` → `aborted`; `POST …/sagas/{id}/retire` `:2474` | plan record stays labelled |
| Runtime binding | created by deployment saga | tenant stamped from plan (`runtime-manager/main.py:234`) | `POST /api/runtime-bindings/{id}/retire` `:747` | route is decorated `mfa_required=True`, but `services/runtime_auth_inbound.py:874-877` enforces MFA only when that flag AND `PANTHEON_RUNTIME_MFA_REQUIRED=true` (default `false`). Deployed policy is **unverified** until Root reads that nonsecret flag; not a proven owner gap and no MFA identity/issuer is required or fabricated (section 0) |
| Telemetry / drift / incident (loop 10) | `POST /api/reconciliation-drift/telemetry-events/consume` `services/reconciliation-drift/main.py:1001`; `POST /api/incidents/consume-drift-report` `services/incidents/main.py:433` | tenant in identity | `POST /api/incidents/{id}/status` `:932` | drift reports are history |
| Evolution decision (loop 11) | `POST /api/evolution/proposals` `services/evolution/main.py:1320`; `/threshold-evaluate` `:2696`; `/daily-sweep` `:1548` | tenant; roles checked | `POST …/proposals/{id}/cancel` `:1968` (proposed/reviewed/approved) | executed decisions are history |

Missing retirement or delete operations (source records, admissions, evidence, completed training
sessions, drift reports, executed evolution decisions) are residuals: labelled and reported, no new API
and no direct database write is authorised.

### A.2 Causal-edge verification (section 5A)

Edge status, using source contracts and existing real owner receipts (e.g.
`docs/deployment/evidence/product-functional-closure/PFG-L12-HUMAN-E2E-LIVE-R2-20260821/deployed-run.json`
carries `dataset_version_id`, `handoff_id`, `governance_handoff_id`;
`docs/deployment/evidence/CAPITAL-FORWARD-HOSTED-ACCEPTANCE-001/evidence.json` carries `binding_id`).

| Edge | Owner contract already supports it? | What is missing | Verdict |
|---|---|---|---|
| 1→2, 2→3, 3→4 | yes, inside research suite | — | supported |
| 4→5 Teaching→Agora | **owner supports it**: `AgoraInteractionEvidenceRequest` has `session_id` and `source_refs: list[str]` (`dataset_extraction/models.py:78-80`) and persists both | the human suite passes its own token `session:session-l12-hl-<run>` (`test_current_human_learning_deployed_e2e.py:599`) instead of the Teaching `session_id` | **caller-input gap only; no owner API gap** |
| 5→6, 6→7 | yes: consult request carries `candidate:`, `dataset:`, `evidence:` context refs, `research_run:` evidence ref and `metadata.agora_handoff_id` (same file `:845-857`) | only that the whole human suite is not fed from loop 4/earlier chain | supported within suite |
| 7→8 Consultation→deployment | `CreateDeploymentPlanRequest` (`services/deployment/models.py:104-125`, `extra=forbid`) has no handoff field; `metadata` is a free dict preserved on the plan, and governance has `consultation-handoffs` (`governance/main.py:327`), but no deployment code reads either | no consumer verifies the `handoff_id`; plan binds only `registry_id` + `approval_decision_id` | **proven owner seam missing as a validated edge; a plan `metadata.consult_handoff_id` caller reference is possible without an owner change but is a recorded reference, not owner enforcement** |
| 2→8 (registry artifact) | runtime suite mutates the pre-existing parent artifact (`test_current_runtime_loops_deployed_e2e.py:732`, `artifact-tw-session-momentum-v1`), then plans on the child | plan does not use the chain's own distilled `registry_id`; `strategy_artifact` is an optional inline projection, applied only when the key is present in registry metadata (`deployment/service.py:477-519`); it is not universally required for paper-plan creation, so its absence alone proves neither a product gap nor executable readiness, and the actual executable artifact/reference path for the distilled entry is unverified | **needs fresh owner GET (A.4)** |
| 8→9, 9→10, 10→11 | yes inside runtime suite | — | supported |
| 11→12 | not an edge (loop policy §3.12) | — | unchanged |

A shared `chain_id` alone is still not causality; each edge must show predecessor ID and consumer
record carrying it, otherwise it is reported unproven.

### A.3 Reusable callers and bounded scopes for confirmed gaps only

Existing entrypoints already do loops 1–4, 5–7 and 8–12 against a deployed stack; no new driver or mode
is justified until the two caller gaps below are fixed in them. Scopes (indicative, to be re-sized by
the implementing task):

* **T2 (suites):** only (1) pass the Teaching `session_id` into the Agora evidence `session_id`/`source_refs`
  in the human suite; (2) add the hosted skip list of section 5 and reject minted tokens;
  (3) record the chain's `registry_id`/`handoff_id` as the plan's predecessor reference (existing `metadata`) if A.4 shows the path carries it.
  The Agora evidence must consume genuine Teaching content (not only the `session_id`); the human suite's fixed
  approve/placeholder/training rows (`test_current_human_learning_deployed_e2e.py:571-603`) cannot be relabelled as causal proof.
  Domain UUIDs differ across suites and `CROSS_LOOP_RUN_ID` is not consumed today.
  Files: `tests/integration/l12/test_current_human_learning_deployed_e2e.py`,
  `test_current_runtime_loops_deployed_e2e.py`, `test_current_research_loops_deployed_e2e.py`,
  `l12_owner_auth.py`, matching unit tests.
* **T3 (stimulus parent):** the existing parent `tests/integration/l12/test_stimulus_cross_loop_deployed_e2e.py`
  (`:265-322` launches the three suites; `:131-168,211-250` normalize/write receipts) already launches and
  normalizes them. Extend it minimally for cross-process phase/resume/`E_close` retirement via owner reads; do not
  build a second suite-launching engine. Ownership: parent exclusively T3; domains and `l12_owner_auth.py` T2.
  `scripts/run_hosted_all12_chain.py` only as a thin invocation adapter if strictly necessary.
* **T4:** workflow job unchanged from section 10. **T5:** read-only Persona Pattern observation.
* No new owner API is authorised by this audit; owner-side gaps (7→8 enforcement, missing retire
  operations) are residuals needing a separate operator decision.

### A.4 Fresh owner GETs for the serial release coordinator (sole Root01a11)

Read-only, only after the exact854 release (live release 39966041271 / 48cfa); none run here. Return the
responses (safe fields only) as evidence:

1. `GET /api/registry/strategy-specs/{registry_id}` (`services/registry/service.py:1481`) for one concrete existing
   distilled ID, or the tenant-scoped strategy-spec list **without** an `artifact_state` filter (`:1520`). Distillation
   emits a *draft* StrategySpec (`source_ingestion/distillation_controller.py:140-160,383-390`) and the response wraps it as
   `RegistryEntryView.entry` (`registry/models.py:320-324`), so read fields under `entry`; an approved-only query
   cannot prove a distilled record is absent. Return `registry_id`, `artifact_state`, `metadata` key names and whether
   `strategy_artifact` is present (presence is informational, not a requirement).
2. `GET /api/deployment/plans/{plan_id}` (`services/deployment/service.py:2275`) for one existing plan
   (or `GET /api/deployment/plans?strategy_id=<id>`): return `plan_id`, `artifact_id`
   (the plan body field, resolved as the registry_id: `deployment/models.py:76-101`, `service.py:1540-1550`; there is no top-level
   `registry_id`), `approval_decision_id` and `metadata` key names. This proves only that **existing** metadata keys are
   preserved; a new arbitrary key round-trip requires a write and is not claimed by GET.
3. `GET /api/runtime-bindings/{binding_id}` (`services/runtime-manager/main.py:722`) for one existing
   binding: return `binding_id` and `metadata.tenant_id`.
4. Nonsecret deployment flag only: value of `PANTHEON_RUNTIME_MFA_REQUIRED` (true/false/unset) for the
   runtime-manager. No token, identity or secret is read or printed.

The chain requires the actual predecessor ID inside each consumer record (section 5A). That is distinct
from optional new owner-side enforcement (e.g. deployment validating `handoff_id`): enforcement is not
required, and no separate product API is asked for unless the source/payload above shows the existing
path cannot carry the predecessor ID.

No hosted L12 suite, owner mutation, VM/HTTP write, source refresh, provider refresh, service restart
or secret access is requested.

### A.5 Preserved constraints

H1 before Alpha admission; H2–H4 with decision-maker versus executing actor recorded separately; no
paper Capital approval; no second-human requirement; no L11→L12 edge; original Pattern six conditions
and independent process-restart proof unchanged.
