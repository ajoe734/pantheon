# Evolution lifecycle, metadata and actions contract

Task: `BFF-EVOLUTION-LIFECYCLE-CONTRACT-DECISION-001` (D-EVOLUTION).  
Owner: Codex. Reviewer: Antigravity2. Date: 2026-09-13.  
Status: **source-backed contract and operator decision packet; D1–D3 remain open**.

This record implements the approved V2 design boundary, not production code.
It does not grant execution, deployment, live trading or capital authority.
Unresolved product choices below prevent claiming this decision task complete.
They do not remove the corresponding U8A/U8B/U8B-FE obligations.

## 1. Authority and observed source

The [V2 design](../02_RESEARCH_AND_DESIGN.md#6-evolution-產品契約與實作設計u7--u8a--u8b)
and [dispatch map](../dispatch-map.json) require six program states, separate
run/experiment state, one program aggregate and the existing execution engine.
L1 [review policy](../../../../EVOLUTION_REVIEW_AND_THRESHOLDS.md),
[cooldown policy](../../../../EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md) and
[stage policy](../../../../PAPER_CANARY_LIVE_POLICY.md) retain precedence.
The [BFF command contract](../../../../services/control-plane/bff/BFF_COMMAND_API_CONTRACT.md)
§8.6–8.7 supplies action names and minimum application roles, not new domain
approval authority. FE risk labels and confirmation dialogs are not approval.

BE inspected base: `b9143d5eb` (full immutable identity in evidence.json).
FE inspected remote dev: `dbe737e0676640f1b9b2395b54fb3c0416099f8a`, verified by
`git ls-remote`; files read with `git show`, not the older local checkout HEAD.
All `execute-plans/` names below denote **ajoe734/execute-plans**, never a
directory to create in Pantheon. Exact source blobs and full granted scopes
are captured in the adjacent task evidence manifest.

| Existing surface | Observation and required disposition |
|---|---|
| `services/control-plane/bff/evolution/router.py`, `_register_evolution_programs_routes` | Create/PATCH call write methods on a read surface; PATCH accepts `name/status/params`. Replace through the U8A write port. The action callback catches `TypeError` and retries a shorter signature; remove that double-submission risk with one typed contract. |
| `services/control-plane/bff/ports/persona_capital_runtime.py`, `EvolutionProjectionPort` | Program runs are reshaped decisions with `decision_id` as `run_id`; candidates are pending decisions. Replace with authoritative linked runs/artifacts. Missing links are unavailable, not invented IDs. |
| `services/control-plane/bff/command_adapters/evolution_adapter.py` | `_execute_program_action` fabricates executed/active and `prog-001`; experiment/job branch similarly fabricates completed. U8A removes fake authority; U8B supplies real program effects; U10A/U10B own experiment/job routing. |
| `services/evolution/main.py`, `models.py`, `client.py` | Existing proposal/review/approval APIs, no program aggregate implementation at this source. Add the program layer inside this service, not BFF or another service. |
| `services/evolution/dispatch_outbox.py`, `dispatch_worker.py`, `dispatch_receipts.py` | Existing durable dispatch, replay, failure/compensation and authoritative terminal readback. Research is the production auto-dispatch adapter; other planes explicitly unsupported until their authoritative integration exists. Scripted test adapters are not production support. |
| FE `src/lib/stateMachines/index.ts` | Six program states (paused is a branch state), separate six-state run and eight-state experiment machines. Preserve every transition below. |
| FE `src/management/pages/EvolutionDetail.tsx` | Resume has no handler; Stop submits `stop`. `mapState` defaults even canonical active/under_review/completed to draft; remove lifecycle aliases/defaulting. Constraints append browser IDs after receipt; replace with owner readback. |
| FE `src/lib/bff-v1/evolution.ts` | Synthesized run/candidate IDs and missing-run-link wildcard can cross-associate candidates; formulas/rules/promotions are constant empty results. Preserve features with qualified owner reads, distinguishing healthy empty from unavailable. |
| FE `PromotionPanel.tsx`, `EvolutionFreezePanel.tsx` under `src/management/components/detail/` | Browser-local promotion history / frozen flag is not owner state; read the real approval, promotion and freeze records. `writes.promoteCandidate` puts candidate ID in memo, which a user memo can replace; require structured candidate/artifact/version fields. |

## 2. Entities and one writer

| Entity | Sole responsibility and identity | Completion/read truth |
|---|---|---|
| Program | Evolution program service; trusted tenant + program_id, durable revision, metadata, membership and control references | Exactly `draft / active / paused / under_review / completed / retired`; never alias a decision/run/approval state. |
| EvolutionDecision | Existing Evolution service + Governance domain policy; tenant + decision_id + target/version | Existing proposed/reviewed/approved/rejected/canceled/executed/superseded lifecycle; risk review, single-active-target and cooldown remain authoritative. |
| Run | Actual downstream research execution owner; source-qualified tenant + run_id, attempt, program_id and decision_id links | `queued / running / paused / completed / failed / cancelled`; owner receipt, not program metadata or command admission. |
| Experiment | Qualified Research owner (U10A/U10B), distinct experiment_id and attempt/run/artifact links | FE experiment lifecycle below remains distinct from ResearchTicket, ExperimentTask and ExperimentRun. |
| Candidate | Real research artifact/registry record with candidate_id, run_id, program_id, artifact_id/version/digest | Read immutable lineage and evaluations; decision IDs do not create candidates. |
| Approval / promotion | Governance/Promotion owns approval decision and deployment plan; Runtime Manager alone owns runtime binding | Read approval/plan/registry outcome and target stage. Program activity never authorizes deployment. |

`services/research/experiments/models.py` has separate ExperimentTask/ExperimentRun
enums (including pending and cancelled); these must not be cast into the FE
experiment enum. U10A qualifies source mapping with
`services/research/main.py`, `services/research/store.py` and
`services/control-plane/bff/research/service.py`. Unknown source state/link must
stay unavailable, preserving original owner state for diagnostics.

## 3. Program metadata and transaction boundary (U8A)

Retain the `/bff/evolution-programs` create/list/detail/PATCH/runs/candidates
read paths. Add the owner API under `/api/evolution/programs` in the granted
`services/evolution/program_router.py`, `program_service.py`, `program_store.py`.
These are planned paths, not implemented endpoints. BFF uses
`services/control-plane/bff/ports/evolution_program_commands.py` for writes;
`ports/read_surface_ports.py` remains read-only. `client.py` binds both typed
ports to the same owner. No direct DB fallback or second BFF cache/writer.

The metadata PATCH allowlist is **`name` only**: nonempty trimmed string.
Reject unknown fields with 422 rather than silently dropping them. In particular
reject `status`, `state`, `params`, tenant/actor/IDs, revision/timestamps,
generation/population/fitness/progress, run/candidate membership, approval refs,
constraints, fitness formulas, mutation rules, budget and deployment fields.
Do not smuggle steering changes through an arbitrary `params` object.
`params` functionality is retained as a typed, versioned configuration obligation
under D3; it is not discarded or claimed delivered by this narrow PATCH.

Creation requires authenticated tenant/actor and a valid name, creates `draft`
with an owner-generated identity and initial revision, and never auto-activates
or dispatches. Preexisting fixture creation of `active` is not production
authority. The create contract must reject client-set status and actor/tenant.
U8A must inventory any stored legacy params and preserve them for audit before
typed migration; unknown executable config cannot be silently activated.

Use `services/foundation/postgres_json_store.py::PostgresJsonOwnerStore`
transaction primitives: current aggregate + revision + idempotency receipt
commit together. The request's expected revision is an explicit precondition,
not a patchable field. Compare it inside the transaction; stale writes return
409 with no partial record or consumed success receipt. Concurrent updates,
restart/readback and storage faults must be verified against real PostgreSQL.

Reuse U3 admission semantics: trusted tenant + authenticated actor + canonical
namespace + idempotency key; hash operation, target, expected revision and
validated payload. An authorized exact replay returns the same outcome before
reapplying current revision checks; changed payload/target is 409. Revalidate
tenant/role on replay. Never use a client-supplied actor or unscoped key.
Healthy empty lists are distinct from failed/unconfigured owner reads (503 /
unavailable); absent or inaccessible IDs follow the existing non-disclosing
404 policy. Authentication/authorization failures remain 401/403.

Metadata CRUD has a durable metadata receipt and owner readback; it does not
manufacture an EvolutionDecision or dispatch outbox entry. U8A may persist
approved control transitions but cannot claim worker effects complete.

## 4. Exact program action vocabulary and transitions

Keep the names in BFF command contract §8.6 and FE evolutionMachine as the
single producer/consumer vocabulary. U8B owns program routing and domain
validation; U8B-FE switches all UI/writes/state-machine consumers together.
U9 owns generic semantic-route migration; do not create a competing entrypoint.
The existing `EvolutionProgramAction` adapter is transport, not a second policy.

Each row requires trusted tenant/actor, same-tenant target/evidence, current
revision, durable idempotency, and permission derived from the same policy
used by detail/nested `allowedActions`. Minimum BFF roles below are necessary,
not sufficient: apply the existing risk-tier Governance review chain as well.
No valid policy/approval evidence means unavailable or rejected, never success.

| Canonical action | Legal program transition | Minimum role, artifact and readback |
|---|---|---|
| `submit_evolution_review` | draft → under_review | operator; immutable program revision/config snapshot and durable `program_activate` review request; read the exact review ID and program revision. Submission is not approval. |
| `approve_program` | under_review → active | approver plus applicable domain review owners; approved `program_activate` request must bind the same tenant/program/config revision. Read approval + active revision; no implicit research job/deploy. |
| `pause_program` | active → paused | operator; record program control outcome. Active-run drain/checkpoint semantics require D1; never mark an individual run paused without its owner acknowledgement. |
| `resume_program` | paused → active | operator; valid current approval/config and no unresolved freeze/policy restriction. Actual continuation/new-attempt semantics require D1. Resume cannot undo cancellation or grant promotion. |
| `complete_program` | active → completed | operator; actual run outcomes and completion evidence. Require no unresolved active work; failures remain failures, not relabelled successful. Read program revision and linked terminal outcomes. |
| `retire_program` | completed → retired | approver and domain policy; durable retirement/audit reference. Preserve history and prohibit new use. Retiring a program does not retire a live strategy or alter runtime bindings. |
| `stop` | unresolved (D1); no seventh program state | operator (existing active caller); real cancellation/drain receipts required. Do not alias to pause/complete/retire without the operator choice. |
| `freeze_generation` | orthogonal generation control, unresolved (D2) | approver plus applicable Governance risk review; bind exact program/generation/revision and durable freeze record. Do not equate generation freeze with a strategy freeze or rollback. |
| `promote_candidate_paper` | program lifecycle unchanged | approver plus paper stage gate; structured candidate_id, run_id, artifact_id/version/digest, approval ID and paper target; Governance/Promotion plan + registry readback. |
| `promote_candidate_live` | program lifecycle unchanged | approver, bound confirmation/MFA where required, and full stage-specific Reviewer/Risk Owner/Operator approval; same artifact chain. No live/capital authorization is granted here. |
| `approve_mutation` / `reject_mutation` | program lifecycle unchanged | approver plus decision risk matrix; explicit mutation review/decision ID, no reinterpretation of program_id as decision_id. U7 supplies identical direct/nested policy; decision API performs real review/approval/rejection. |

Other lifecycle edges remain invalid (409), including draft→active by PATCH,
paused→completed, active→retired and retired→active. A rejected or
changes-requested activation approval is shown as such; a new program transition
out of under_review is not invented by this task. Any requested new edge needs
an explicit product decision; the existing review feature remains usable to
inspect the outcome. Owner `allowedActions` must prevent unsupported retries.

Remove old FE `pause` for program targets in favor of `pause_program`, and
`promote_paper/live` in favor of `promote_candidate_paper/live`; do not remove
those names for unrelated domains. No permanent aliases or unknown-state→draft
fallback. Direct and semantic action paths must converge on one owner during
ordered BE→FE delivery; U9 retires old API producers, imports, DI, OpenAPI,
clients and mocks at the accepted pair boundary, not by changing this document.

## 5. Run and experiment actions retained independently

The existing FE state machines define the following complete edge inventory.
Worker-reported events are not operator commands. BFF read/command permissions
must not let a caller report `complete` or `job_completed` as authoritative.

| Entity | Exact source → destination / action inventory | Owner and proof |
|---|---|---|
| Run | queued→running `start`; running→paused `pause`; paused→running `resume`; running→completed `complete`; running→failed `fail`; queued/running→cancelled `cancel` | Research execution owner, immutable run/attempt and matching worker receipts. A cancel request/202 is not proof of stopped execution; late completion must respect a cancellation fence. Paused→cancelled is not in the existing machine: D1 must explicitly settle stop's coverage. |
| Experiment | draft→queued `run_experiment`; queued→running `job_started`; running→completed `job_completed`; running→failed `job_failed` | Research owner and real attempt/worker linkage, not an Evolution program metadata mutation. |
| Experiment review | completed→attached_to_review `attach_to_review`; completed→invalidated `invalidate_result` | operator for attachment plus required review workflow; approver for invalidation; valid artifact, target review identity, reason and durable owner/audit readback. Attachment is not approval. Invalidated evidence cannot be promoted. |
| Experiment retry/archive | failed→queued `retry`; completed→archived `archive` | operator, qualified owner policy; retry creates one linked new attempt per key and preserves failed attempt evidence. Archive changes visibility, never implies physical deletion. |
| Experiment promotion | `promote_artifact`, distinct from program promotion | approver plus existing artifact/stage approval; true artifact/registry and plan readback. Do not infer new Experiment state from deployment outcome. |

No cancel-only reduction of experiment features is permitted. Program, run,
experiment and job states do not share an enum. U10A/U10B and D-JOBS own
qualification of the real Research sources, supported control APIs and retention
policy; missing support stays an outstanding obligation, not a passing 503 test.

## 6. Steering, freeze and promotion obligations

| Feature and FE consumer | Retained owner contract / acceptance boundary |
|---|---|
| Constraints: `EvolutionDetail.tsx`, `src/lib/v3/evolutionSchemas.ts` | Retain typed hard/soft fields, operators, values, penalty weight and enabled state. `create_constraint` currently transmits free text in memo; parsing, edit/delete and application policy are not proven. Proposed D3: versioned program-scoped configuration, Governance `constraint_change` approval, future-run pinning and durable readback. Never evaluate arbitrary expressions as code. |
| Fitness: `FitnessFormulaPanel.tsx`, `evolution.ts` | Retain formula list/detail, ID/version, expression, metrics and applied scope. D3 chooses ownership/application rules; proposed program-scoped versions and `fitness_formula_change` review. No constant empty success or BFF shadow formula registry. |
| Mutation: `MutationRuleManager.tsx`, `evolution.ts` | Preserve list, add and enable/disable obligations, scope/expression/rate/risk. Current add control is nonproduction and toggle disabled. D3 proposes program-scoped versioned configuration, reviewed before future runs; do not conflate rule editing with approving an EvolutionDecision mutation. |
| Budget changes | FE approval type `budget_increase` already exists; params cannot bypass it. This record introduces no capital operation or new allocation authority. |
| Generation freeze: `EvolutionFreezePanel.tsx` | D2 must fix scope, existing runs, promotion and release semantics; read owner freeze record after command outcome. No optimistic frozen flag as truth. |
| Promotion: `PromotionPanel.tsx` | Preserve paper/live buttons, candidate comparison and history, with true artifact lineage and stage-specific approval. Remove `pr_local_*`; do not encode candidate identity in memo. Read actual registry/plan records and terminal failure as well as success. |

## 7. Existing execution chain and evidence semantics (U8B)

Only operations actually representing an approved EvolutionDecision flow through
the existing approval → dispatch outbox → dispatch worker → downstream owner →
terminal receipt chain. Program CRUD does not create artificial work. Program
controls invoke the actual run owner's controls; never add a program polling,
retry or dispatch engine. If the real owner lacks pause/cancel, keep that action
unavailable and its success-case obligation open until the implementation task
supplies owner effects under the existing execution architecture.

The outbox owns dispatch identity, attempt and retry/dead-letter/compensation
state. Link tenant, program revision, decision ID, actual downstream run ID,
candidate/artifact/approval and terminal reference explicitly. Never match
records by display text, substitute default IDs, or accept caller-asserted
success. Approval and durable intent must retain the existing atomic/recovery
guarantees; restart reconciliation reuses identity instead of spawning work.

There is an existing semantic mismatch to preserve visibly: L1 review policy
§12 describes `executed` at authoritative downstream acceptance; current
`services/evolution/main.py::execute_proposal` and `dispatch_receipts.py` enforce
terminal downstream evidence and only successful readback closes execution.
This decision does **not** weaken that runtime gate or rewrite L1. Expose
acceptance/reference separately from terminal outcome; a 202 is neither
successful execution nor Program/Run completion. Any future decision to change
the canonical `executed` milestone requires explicit L1 reconciliation, outside
this source scope. Cooldown/observation retain the L1 acceptance-time boundary;
U8B must test it separately from the terminal completion timestamp and report
any implementation divergence instead of silently moving the clock.

## 8. Operator decisions required before finalization

No current operator answer is recorded. These are concrete proposals, **not
approved rules**. On reply record the exact chosen scope in this decision and
evidence before independent review; do not infer approval from elapsed time.

| ID | Missing authority / question | Proposed choice and alternative | Blocking obligation |
|---|---|---|---|
| D1 | Stop, program pause/resume and effects on active/paused runs are not defined by §8.6 names or FE buttons. | Proposed: pause stops new generation admission while current runs drain; stop additionally cancels all nonterminal runs (including paused) and reaches program paused only after owner stop receipts; resume schedules eligible new attempts, never revives cancelled ones. Alternative: Stop is only the same drain behavior as Pause (requires explicit acceptance of alias removal and UI wording). | U8B control state/fencing and U8B-FE Stop/Resume; no implementation may invent which runs are killed or resumed. |
| D2 | Generation freeze has no durable owner record or scope/release semantics. | Proposed: seal current generation membership and artifact versions, prevent additions/mutations and promotion, allow in-flight runs to finish; release requires the same Governance review. Alternative: also stop current generation runs, requiring D1 owner receipts. Neither choice freezes a live runtime. | U8A control record shape; U8B freeze/release and U8B-FE readback. |
| D3 | Formula/rule scope and approval/application timing are not specified by display-only FE records. | Proposed: program-scoped, versioned steering; Governance-approved changes affect future runs only, existing attempts pin their original config. Alternative: a separate explicit protocol for editing active runs, including owner checkpoint and approval semantics. Preserve all legacy params for migration; add no global competing registry. | U8A typed configuration and U8B constraints/formulas/rules; FE edits remain undelivered until true effects/readback exist. |

The six existing program edges, entity separation, metadata safety, tenant and
replay boundaries, approved-decision engine and feature inventory are already
source-backed. D1–D3 only ask for genuinely missing semantics. A packet review
may assess these findings, but cannot substitute for an operator product choice.

## 9. Ordered ownership, cleanup and validation

The machine-readable evidence includes exact `dispatch-map.json` write sets
for U7, U8A, U8B and U8B-FE, including planned files. They are snapshots, not a
new execution grant. Current TaskStore dependency/lease truth controls dispatch.
U7 supplies mutation review/journal policy; U8A supplies the program data owner;
U10A removes competing experiment/job adapter entries before U8B edits the
shared registry/adapter/catalog; U8B-FE consumes that BE version. U9 owns generic
API retirement. No task concurrently edits shared main/router/adapter files.

| Validation group | Required evidence and negative cases | Delivery owner |
|---|---|---|
| Program lifecycle | All six states and each edge above; every disallowed edge; missing/rejected/wrong-revision/foreign-tenant approval; PATCH status/params/identity rejection; direct and nested allowedActions agree. | U8A/U8B + FE |
| Data/replay | Real PostgreSQL create/get/list/name update; 20 same-key concurrent callers; changed body/target/actor/tenant; two independent instances/processes; stale revision; transaction rollback, crash and restart; legitimate reviewer read visibility. | U8A |
| Real effects | Each control and retained steering action: trigger→owner→durable effect→readback; pause/stop acknowledgement, late worker result fence, retry lineage, unknown owner, timeout/auth/storage failure, freeze scope and release, promotion artifact/approval/stage mismatch. | U8B/U10B |
| Dispatch | Approved-only, duplicate intent, lease/crash recovery, terminal success/failure, pending versus transport retry, unsupported plane, compensation/dead letter and replay cooldown. | Existing dispatch full-file regression + U8B |
| Projection/UI | Real program/run/candidate links, missing link not wildcard; canonical active/under_review/completed; resume handler; constraints/freeze/promotion readback after reload; permission/error/empty/unavailable; no client-created identities/history. | U7/U8B-FE |
| Removal | Delete read-port writes, direct DB fallback, fake receipts/default IDs, TypeError retry, nested policy copies, old program aliases and local fake records. Audit imports, DI, registry unique match, OpenAPI and FE URL producers/mocks; exercise routes after static searches. | Scoped source owners; U9 API cutover |

Preserve the original inventory's per-case business and negative assertions;
`services/control-plane/bff/tests/bff_test_architecture_inventory.json` is a
file inventory, not a passed-test count. Existing B10 eight review/journal
failures remain U7 obligations. Relevant full files include
`test_evolution_center_contract.py`, `test_ew05_mutation_review_contract.py`,
`test_bff_evolution_experiment_jobs_events_contract.py`,
`tests/test_bff_b2_002_evolution_jobs_ops.py`,
`tests/test_bff_b3_evolution_journal.py`,
`tests/test_evolution_programs_population_contract.py`, and
`tests/test_evolution_router.py` under BFF; plus
`services/evolution/test_dispatch_worker.py`,
`test_l12_evo_001_process_recovery.py` and
`tests/test_postmortem_evolution_decision_e2e.py` under Evolution.
Run focused new cases and affected full files after implementation, and the
original B10/full migration inventory before their own closeout. Existing
router doubles characterize behavior only; they do not prove a durable owner.

This decision's local results are in evidence.json with command, exit status,
collection and actual counts. No runtime deletion, 61-file regression, DB
acceptance, FE build, hosted pair or deployment is claimed here. Unimplemented
positive cases cannot be replaced with skipped tests or expected 503s to claim
feature completion. Publish the two task artifacts, obtain exact-head review
only after operator choices are resolved, and let the supervisor integrator
merge before owner `done`.
