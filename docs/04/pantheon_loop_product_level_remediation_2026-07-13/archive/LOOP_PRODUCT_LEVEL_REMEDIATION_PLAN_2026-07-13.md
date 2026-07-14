# Pantheon Loop Product-Level Remediation Master Plan

Document status: archived planning baseline; execution remains active until the
program closeout task proves every exit criterion

Planning baseline: 2026-07-13

Baseline repository: `ajoe734/pantheon`

Baseline branch and merge target: `origin/dev`

Baseline commit when the clean planning worktree was created:
`349249e8f5ab1a89f82afd71925eb99342d66ed1`

Primary execution packet:
`docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/INDEX.md`

Machine baseline audit:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/BASELINE_AUDIT_2026-07-13.json`

Additive execution audit:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/REMEDIATION_GAP_ADDENDUM_2026-07-13.md`

## 1. Objective

This program closes the difference between "code exists" and "the product
loop works." It covers the twelve loops declared by
`LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md`, adds the Per-Persona OODA loop
declared by the master system analysis, and closes the product programs that
cross those loops: Persona promotion/allocation, Trade Journey, Persona
Interaction, and Management AI/OpenClaw repair.

The required end state is not a larger collection of APIs, fixtures, or unit
tests. The required end state is:

1. every required transition has one authoritative runtime owner;
2. the owner is started by the target deployment without an undocumented
   profile or manual shell step;
3. accepted commands cause the intended downstream state change, or return an
   explicit terminal failure;
4. state, receipts, and idempotency survive process and stack restart;
5. operator surfaces read authoritative desired state, controller health,
   actual state, and downstream readback;
6. safety, RBAC, MFA, two-person, environment, and capital boundaries remain
   fail-closed;
7. target-environment evidence proves the happy path, duplicate path, failure
   path, recovery path, and hosted user path where a frontend exists;
8. the loop registry and task archive report no maturity stronger than the
   evidence supports.

## 2. Authority And Inputs

This plan extends rather than replaces the following authority:

- `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md`
- `docs/04/pantheon_sa/SA-21_global_loop_inventory_autopilot_execution_plan.md`
- `Pantheon_總索引版系統分析文件.md`, Per-Persona OODA section
- `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`
- `DELIVERY_CLOSURE_AND_LOOP_STATES.md`
- `PAPER_CANARY_LIVE_POLICY.md`
- `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`
- `ROLLBACK_AND_POSITION_SEMANTICS.md`
- `BINDING_AND_DEPLOYMENT_SEMANTICS.md`
- `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md`
- `docs/conventions/WAVE_PLANNING_PARALLELISM.md`

Existing active work is an input, not work to duplicate. In particular this
program composes with:

- `PPL-ALLOC-010` through `PPL-ALLOC-013` and the blocked `PPL-ALLOC-009`
  closeout;
- `TJ-E2E-014`, the existing `TJ-E2E-012` closeout, and the prior TJ tasks;
- `PINT-010-R2` and `OPS-EP-DEV-MAIN-RECONCILE-001`;
- `EVOCHAIN-001` through `EVOCHAIN-011`;
- the completed OpenClaw persona cron and cron-to-OODA packet work.

## 3. Product-Level Definition

### 3.1 Evidence levels

The program uses the existing maturity vocabulary and adds a strict
product-level admission rule:

| Level | What it proves | What it does not prove |
| --- | --- | --- |
| component | schema, function, class, route, or UI component exists | no runtime ownership or side effect |
| contract | focused tests prove request/response and local state behavior | no default deployment or recovery |
| integrated | more than one process or repository works in a controlled test | no target-host liveness |
| reconciled | a durable controller compares desired and actual state, repairs drift, and exposes health | no target-host recovery unless exercised |
| proven-live | target deployment proves liveness, duplicate safety, restart/recovery, downstream readback, and operator truth | no broader product UX unless exercised |
| product-level | proven-live plus security negatives, hosted desktop/mobile UX where applicable, release identity, rollback/degraded behavior, independent review, and archived evidence | nothing in this program may claim more than this evidence |

Unit tests, a static registry, a seed, a snapshot, a manually invoked script,
an HTTP 200, a submitted command, or a locally manufactured receipt cannot by
itself satisfy `reconciled`, `proven-live`, or `product-level`.

### 3.2 Mandatory proof bundle per task

Every primary task must archive:

- task id, owner, reviewer, repository, branch, base branch, PR, and merge SHA;
- exact changed-file scope and authoritative write owner;
- local test/build/static validation output;
- target deployment identity or a reason deployment is not applicable;
- request, receipt, downstream readback, and audit/trace correlation;
- duplicate/idempotency evidence;
- restart/recovery evidence for a durable worker or store;
- unauthorized, missing-approval, wrong-environment, or unsafe-action negative
  evidence where the task exposes a command;
- hosted desktop and mobile evidence for user-visible frontend work;
- failure/degraded and rollback evidence;
- residual risks, named owner, recheck trigger, and expiry;
- independent reviewer verdict.

If any required item is unavailable, the task remains `in_progress`, `review`,
or explicitly blocked. It must not be archived as completed.

### 3.3 Program gates

The execution DAG advances through seven fail-closed gates:

| Gate | Required proof |
| --- | --- |
| G0 planning integrity | twelve L1 loops plus the separately classified Per-Persona OODA overlay, unique task ids, an acyclic DAG, explicit consumption of existing work, planner/fleet implementation separation, canonical run/worktree/scope provenance, and distinct formal review |
| G1 release and security | strict scoped dev auth, no browser bearer or secret, complete viewer/read and privileged-negative route matrix, safe writes by default, exact-SHA paired FE/BFF gate-before-deploy, one cutover lease, candidate probes, and two-sided rollback |
| G2 real execution | default deployment owner, durable trigger, canonical side effect, and terminal downstream readback for every loop |
| G3 recovery and truth | duplicate, lease, timeout, DLQ/replay, worker/BFF/database/full-stack restart, and controller records rather than registry-only liveness |
| G4 product paths | target-dev Knowledge, Execution, Human Interaction, and Management Repair scenarios pass end to end |
| G5 hosted UX and governance | authenticated desktop/mobile, accessibility, strict performance, SSE recovery, degraded/error behavior, RBAC, tenant, MFA, and two-person positives and negatives |
| G6 evidence and closeout | checksummed machine evidence, canonical fleet delivery provenance, exact PR/merge/deploy identities, formal review by a distinct admitted runtime identity, evidence-derived maturity, and no unresolved blocking product risk |

Failure at a gate blocks later maturity promotion. An emergency release override
may never bypass authentication, credential, artifact-integrity, or
no-live-capital checks and must record approver, reason, scope, and expiry.

## 4. Audited Baseline

The audit found a large difference between task closure and runtime closure:

- all 37 primary `LOOP-AUTO-*` tasks were terminal in the archive;
- the live loop-health read model returned twelve loop rows but zero live
  loops, zero reconciled loops, and zero controller-health records;
- eleven rows were still `api-only`; Capital Pool Execution was `manual`;
- the read model was degraded and served registry metadata rather than
  controller snapshots;
- focused controller and BFF tests passed, demonstrating component quality but
  not target-deployment closure;
- multiple workers existed only behind opt-in Compose profiles, and several
  loop workers had no deployment service at all;
- several accepted or executed-looking paths did not perform their claimed
  downstream side effect.

The frontend delivery audit additionally found:

- `dev` pushes deployed independently of the FE-BFF integration gate;
- the release symlink changed before hosted probes completed and there was no
  automatic rollback;
- a failed deploy workflow could therefore leave the failed candidate live;
- the hosted bundle enabled real and dev-stub writes by default;
- a build-time dev bearer fallback was available to browser requests;
- `execute-plans/main` and `execute-plans/dev` had diverged, leaving some
  merged Persona Interaction work outside the deployed branch.

These facts establish the planning baseline. They do not freeze current
runtime state; each implementation task must re-audit its scope before editing
and record any newer evidence.

## 5. Loop Inventory And Required End State

### 5.1 Source Ingestion

Current weakest segment: connector/schedule APIs exist, but persona source
requirements do not continuously reconcile into connector registrations and
schedules, and the scheduler is not a default target-deployment process.

Required end state:

- persona/data requirement is desired state;
- connector registration, schedule, last fetch, normalized record, and source
  health are actual state;
- an idempotent reconciler provisions and repairs the difference;
- scheduler and reconciler are default, supervised, restart-safe processes;
- a new or changed requirement is proven to create/update a schedule and
  produce a real `SourceRecord` with provenance;
- loop health exposes heartbeat, success/failure, lag, and evidence.

### 5.2 Strategy Distillation

Current weakest segment: a distillation worker implementation exists without a
default durable event consumer that owns `SourceRecord` to mutable
`StrategySpec` draft.

Required end state:

- normalized-source events enter a durable inbox/outbox;
- a supervised worker writes only mutable draft heads;
- duplicate and out-of-order events are harmless;
- approved immutable artifacts are never modified;
- catch-up/replay and restart behavior are proven;
- draft and evidence lineage are readable by operators.

### 5.3 Alpha Replication

Current weakest segment: revalidation logic exists without a default queue
owner, scheduled worker, or authoritative experiment writeback.

Required end state:

- reviewed StrategySpec snapshots enter a durable replication queue;
- scheduled revalidation and explicit commands share the same idempotent
  worker contract;
- a real `ExperimentRun` and evidence lineage are persisted;
- production activation remains disabled until ordinary approval/deployment
  gates accept an immutable artifact;
- failure, retry, restart, and operator readback are proven.

### 5.4 Persona Teaching

Current weakest segment: the preview worker is opt-in and preview evaluation
uses stub OHLCV/dataset references that can generate a passed proof.

Required end state:

- evaluation reads an authoritative dataset with provenance and freshness;
- missing/invalid/insufficient data fails closed and cannot produce a passing
  commit gate;
- thresholds and policy version are recorded;
- the preview worker is supervised and default in the required deployment;
- teaching commit mutates only the governed persona target after evaluation
  and approval;
- restart, duplicate job, stale candidate, and negative-gate proofs exist.

### 5.5 Agora / Human Trader Interaction Evidence

Current strongest segment: several Agora stores have target-host persistence
evidence and market-data projection has live source provenance.

Remaining end state:

- interaction, feedback, note, journal, and insight events enter a durable
  evidence inbox;
- dataset extraction and handoff generation are background-owned, idempotent,
  tenant-scoped, and restart-safe;
- evidence can feed Observe/Learn and imitation datasets only;
- no path promotes an artifact or changes live execution directly;
- contract compatibility is release-gated across Pantheon and execute-plans.

### 5.6 Human Imitation / Shadow Evaluation

Current weakest segment: scheduler and service components exist but the
trace-to-dataset-to-evaluation-to-candidate chain is not a default reconciled
process.

Required end state:

- governed trace selection produces versioned datasets;
- scheduled shadow/OOS evaluation consumes only eligible datasets;
- results persist to an immutable candidate with evidence;
- production adapters remain disabled until standard experiment, approval,
  and deployment gates complete;
- scheduler health, backlog, retry, restart, and duplicate behavior are
  operator-visible.

### 5.7 Consultation

Current weakest segment: workflow code is not deployed as a default worker and
can manufacture a committee identity, memo, recommendation, publication, and
handoff without a real reviewer/provider decision.

Required end state:

- submitted requests persist and are assigned to eligible real participants or
  an explicitly configured provider;
- unavailable participants leave the request waiting or blocked, never
  auto-approved;
- memo publication and governance handoff require verifiable authorship and
  review evidence;
- the worker is supervised, idempotent, restart-safe, and visible;
- advisory output never directly deploys or trades.

### 5.8 Promotion / Deployment

Current weakest segment: the outbox consumer is opt-in and consumes receipts
without an apply callback that performs runtime-manager side effects.

Required end state:

- an approved immutable artifact and plan create a durable deployment command;
- the dispatcher calls the canonical runtime authority;
- a `RuntimeBinding` or terminal error is read back before success;
- persona creation remains `provisioning` until this readback exists;
- retries, duplicates, compensation, rollback, restart, and kill interruption
  are proven;
- submitted is never presented as applied/running.

### 5.9 Capital Pool Execution

Current strongest segment: paper runtime and fleet reconciler exist and the
consumer path has historical evidence when a signal is manually supplied.

Remaining end state:

- active paper bindings reconcile into exactly one supervised worker;
- a bounded dev/paper-only signal source discovers eligible bindings and emits
  schema-valid, binding-scoped signals without enabling live capital;
- signal, decision, order, fill, position, heartbeat, stop, restart, and
  isolation are proven;
- queues cannot leak across bindings;
- live mode remains disabled unless separately human-approved;
- loop-run and Trade Journey truth derive from actual events.

### 5.10 Telemetry / Reconciliation

Current weakest segment: telemetry APIs exist, while drift consumers,
schedulers, and incident listeners are opt-in or fixture-oriented and the full
chain is not default-owned.

Required end state:

- runtime/operator events enter durable telemetry;
- scheduled and incident-triggered reconciliation compare real authoritative
  sources rather than returning an empty green result;
- drift creates a durable report and a deduplicated incident;
- incident resolution can create a postmortem and evolution proposal;
- Trade Journey receives first-class correlated events rather than relying on
  a manual rebuild;
- backlog, freshness, restart, replay, and failure truth are visible.

### 5.11 Evolution

Current active work: `EVOCHAIN-*` is closing threshold, incident, postmortem,
store, journal, and sweep gaps.

Remaining end state after EVOCHAIN:

- an approved evolution decision is dispatched to the correct canonical
  deployment/governance/runtime authority;
- `SUBMITTED` is not manufactured as an execution result without delivery;
- terminal receipt and target post-state are persisted;
- freeze, risk-off, rollback, and redeploy remain governed and compensatable;
- cooldown, singleton, duplicate, restart, and negative authorization paths are
  proven;
- formal journal truth links decision, command, receipt, and target readback.

### 5.12 BFF Health Monitoring

Current strongest segment: the BFF process has a health endpoint and a
background monitor implementation.

Remaining end state:

- each downstream uses its actual readiness/health contract;
- monitor telemetry carries valid canonical identities and does not silently
  dead-letter;
- probe, error-rate, recovery, incident, and degraded-mode transitions are
  durable;
- loop-health reads controller snapshots and downstream actual state instead
  of registry-only metadata;
- operator surfaces distinguish unavailable, stale, snapshot, scheduled,
  reconciled, and proven-live truth.

### 5.13 Per-Persona OODA

Current strongest segment: cron registration, cron execution, OODA packet
persistence, and several paper OODA packet contracts have been implemented.

Inventory gap: this loop is defined by the master system analysis but has no
stable canonical loop id in the twelve-loop registry, and target-host
restart/default-activation evidence is not represented in loop health.

Required end state:

- add stable loop id `per_persona_ooda` without changing the L1 claim that
  Capital Pool Execution is the only continuously resident execution loop;
- every eligible persona has an idempotently reconciled schedule;
- one cron run maps to one real agent turn and one persisted packet or one
  explicit terminal failure;
- Observe, Orient, Decide, Act-proposal-only, and Learn evidence are linked;
- no OODA path directly trades or mutates live capital;
- restart, orphan schedule repair, duplicate run, and operator truth are
  proven.

## 6. Cross-Program Product Gaps

### 6.1 Persona Promotion And Allocation

The program consumes `PPL-ALLOC-010..013` and does not recreate them. Final
acceptance must prove:

- real telemetry attribution resolves through binding/ledger/persona identity;
- rebalance proposal, approval, terminal apply, authoritative weights, and
  restart-safe receipt agree;
- quarterly ranking carries stage, current weight, evidence, and immutable
  snapshot identity;
- containment is admitted with legitimate two-person evidence and produces a
  terminal safe post-state;
- create-to-paper, paper-to-review, allocation, containment, hosted UX, and
  residual-risk evidence are archived together.

### 6.2 Trade Journey

The program consumes `TJ-E2E-014` and the prior TJ work. Additional work must:

- replace the production default `ACTION_DISPATCH_UNAVAILABLE` path with a
  canonical governed dispatcher;
- add the missing frontend controls and receipt/refetch behavior;
- preserve RBAC, MFA, idempotency, stale revision, live-action feature gate,
  and partial-failure behavior;
- prove one real paper trade forms one correlated multi-stage journey;
- prove desktop/mobile, SSE, partial-source rendering, replay, restart, and
  action readback before final closeout.

### 6.3 Persona Interaction

The program consumes `OPS-EP-DEV-MAIN-RECONCILE-001` and `PINT-010-R2`.
Completion requires a reviewed, contract-aware branch reconciliation, a green
integration gate, an exact deployed commit containing the feature chain, and
authenticated desktop/mobile evidence for consultation, disagreement,
proposal revision, paper validation, journal reflection, audit readback, and
degraded/rollback behavior.

### 6.4 Management AI / OpenClaw Repair

Provider readiness and unit tests are insufficient. Product completion
requires an authorized hosted flow:

1. activate `kernel_repair` control mode;
2. prepare a clean scoped worktree;
3. send the returned repair metadata with the Management AI conversation;
4. write and read back a harmless sentinel inside the declared scope;
5. generate SA/SD output and a task packet;
6. enqueue it through the assistant dev bridge;
7. prove the supervisor processed it;
8. deactivate control mode and prove writes fail closed afterward.

No test may use `.` as the write scope, the shared live checkout, or a real
capital/broker mutation.

### 6.5 Strategy Workshop deferred operations

The completed Agora gap work intentionally left six v1.5 operations
fail-closed with HTTP 501. Product completion must implement GET/POST versions,
select version, create research run, create consultation, and conclude through
canonical stores and governed commands. The exact OpenAPI/bundle digest and
generated execute-plans client must agree; each hosted action must show a
terminal readback, stale-revision and authorization negatives, and degraded
behavior. Keeping the operations visibly unavailable was an honest interim
state, not product completion.

## 7. Cross-Cutting Architecture Work

### 7.1 Safe frontend delivery

The frontend delivery task must:

- make integration/release gate success on the exact SHA a prerequisite to
  deployment;
- build/install a candidate without changing the live symlink;
- probe candidate assets, strict-live BFF compatibility, browser routes, and
  safe write posture before switch;
- switch atomically only after candidate acceptance;
- rollback automatically when post-switch smoke fails;
- default real writes and dev-stub writes to false unless an explicit,
  auditable operator input enables them;
- remove build-time bearer credentials from browser fallback behavior;
- record previous SHA, candidate SHA, gate run, switch, probes, and rollback;
- enforce Agora and general FE/BFF contract compatibility before release.

Dev authentication must run with the BFF auth stub disabled. CI and hosted
tests use short-lived tenant- and role-scoped identities from the existing dev
login contract; viewer, operator, approver, risk owner, and the two distinct
containment operators must not share an all-role browser token. The BFF exposes
a non-sensitive exact build identity containing git SHA, image digest, build
time, environment, and configuration posture so a verifier can reject an
unknown backend deployment.

### 7.2 Runtime loop truth

Static catalog metadata remains useful but cannot be the live source. The
truth substrate must provide:

- stable loop id and controller owner;
- desired-state reference and query;
- actual-state query and downstream source;
- last heartbeat, tick, success, failure, and repair;
- backlog/lag, current lease, and duplicate suppression key;
- deployment identity and version;
- evidence refs and truth level;
- staleness and accepted-live calculation;
- append/update API or durable writer library with tenant/environment scope.

Every loop implementation task writes this contract. BFF composes it without
inventing liveness. Only target-environment evidence may promote a loop to
`proven-live` or `product-level`.

### 7.3 Anti-false-closure gate

A machine verifier must reject a primary task closeout when required evidence
is absent. At minimum it rejects:

- no merged PR or merge SHA;
- target repo mismatch or phantom cross-repo delivery;
- mock/fixture-only evidence for a live claim;
- missing deployment identity for a deployed task;
- command success without terminal downstream readback;
- missing restart/recovery evidence for a durable loop;
- frontend work without hosted desktop/mobile proof;
- unsafe write defaults, missing security negatives, or missing independent
  reviewer;
- maturity promotion unsupported by loop-health truth.

The guard must not rewrite existing task history. It applies to this program's
primary tasks and emits an explicit blocked/review finding for the owner.

### 7.4 Recovery harness and evidence manifest

A shared target-dev harness must inject failure before and after outbox
persistence, before and after downstream mutation, after downstream success but
before receipt persistence, and before projection update. It also exercises
worker, BFF, database, and full-stack restart, duplicate delivery, lease
expiry, and network timeout. Admitted commands require RPO zero and recovery
within two accelerated test controller intervals without duplicate canonical
effects. Production cadence is not silently changed.

Each run writes an append-only, checksummed `evidence.json` containing
requirement ids, conclusion, exact evidence files, FE/BFF SHAs, image digest,
PR/check runs, correlation and idempotency ids, pre/post canonical readback,
restart/rollback/security outcomes, and reviewer verdict. `missing`,
`indirect`, `stale`, or `contradicted` evidence cannot pass.

## 8. Execution Strategy

The machine-readable DAG is canonical in `tasks.json`. The execution waves are:

### Wave 0 — Safety, truth, and closure enforcement

- safe execute-plans release pipeline;
- strict dev-auth cutover and exact BFF build identity;
- controller-health store/writer contract;
- loop inventory and verification correction;
- product-evidence schema and anti-false-closure guard;
- reusable full-stack failure-injection and recovery harness.

### Wave 1 — Independent loop owners

After the truth contract is available, independent workers may proceed in
parallel where their file and authority scopes do not overlap:

- source, distillation, alpha;
- teaching, Agora evidence, imitation, consultation;
- the six deferred Strategy Workshop operations and their generated client;
- deployment, capital, telemetry, BFF health, Per-Persona OODA;
- evolution target-plane dispatch after the active EVOCHAIN packet closes.

Tasks that touch the same service or high-churn BFF main/read-store surfaces
carry explicit dependencies even if that narrows the frontier.

### Wave 2 — Cross-loop product paths

- persona provisioning through canonical runtime readback;
- Trade Journey governed actions split into backend and frontend tasks;
- Management AI repair split into backend/ops proof and frontend hosted proof;
- execution-spine and knowledge-spine target-host verifiers.

### Wave 3 — Existing program convergence

- PPL product closeout;
- TJ product closeout;
- PINT product closeout.

These tasks consume rather than replace existing active work.

### Wave 4 — Global product closeout

Run a clean target-host drill across the twelve canonical loops plus the
Per-Persona OODA composite overlay, reconcile registry and loop-health truth
from evidence, archive the result, and obtain independent operator review.

## 9. Required Product Scenarios

### Scenario A — Knowledge spine

```text
persona source requirement
  -> connector/schedule reconciliation
  -> real SourceRecord
  -> durable distillation job
  -> StrategySpec draft
  -> reviewed alpha replication queue
  -> ExperimentRun
  -> governed teaching / consultation / evidence handoff
```

Proof must include duplicate delivery, worker restart, failed source/provider,
stale draft, and immutable approved-artifact protection.

### Scenario B — Execution spine

```text
create paper persona (provisioning)
  -> canonical DeploymentPlan
  -> deployment dispatcher
  -> RuntimeBinding readback
  -> one supervised paper worker
  -> bounded paper signal
  -> decision/order/fill/position
  -> telemetry + canonical loop-run/first-class Trade Journey projector
  -> drift/incident/postmortem
  -> EvolutionDecision proposal
  -> governed target-plane command + post-state readback
```

Proof must include stack restart, duplicate command, binding isolation, stop,
rollback/degraded behavior, and no live-capital side effects.

### Scenario C — Human interaction and learning

```text
Agora interaction
  -> durable evidence
  -> dataset/handoff
  -> consultation or shadow evaluation
  -> governed candidate/proposal
  -> human decision
  -> journal/OODA Learn evidence
```

Proof must include tenant isolation, unavailable reviewer/provider,
unauthorized identity, rejected proposal, and no direct deployment/trading.

### Scenario D — Management repair

Use the scoped hosted Management AI/OpenClaw flow described in section 6.4.
The sentinel must be harmless, task-scoped, read back, and cleaned up or left as
an explicitly archived test artifact according to the task evidence packet.

## 10. Release And Safety Invariants

- All new execution remains paper/dev unless an existing policy and legitimate
  Human/Ops gate explicitly authorizes more.
- No task may activate real broker or real capital.
- Real/stub frontend writes default false.
- No bearer, API key, or privileged credential is compiled into a browser
  bundle or archived in evidence.
- Dev auth stub is disabled for hosted product evidence; fixed all-role tokens
  and privileged local/session storage fallbacks are prohibited.
- A task cannot self-manufacture the second operator in a two-person flow.
- A seed, snapshot, generated fixture, or local overlay is labeled and cannot
  satisfy authoritative readback.
- `submitted`, `accepted`, or `queued` is not `applied`, `running`, or
  `executed` without terminal target readback.
- Persona OODA and learning paths are proposal/evidence only and cannot call
  LEAN or a broker directly.
- Only Capital Pool Execution is a continuously resident execution loop; other
  loops use their declared event, command, cron, or monitor trigger model.
- Repair work uses a clean scoped worktree and never the shared live checkout.

## 11. Fleet Delivery Rules

Every fleet owner must:

1. re-audit current `origin/dev`, active tasks, and overlapping work before
   editing;
2. use a clean task branch/worktree and the correct repository;
3. keep Pantheon and execute-plans changes in their respective repositories;
4. split cross-repo backend/frontend work as declared by the DAG;
5. stage only owned files;
6. run focused and adjacent validation;
7. commit with required subject and trailers;
8. push, open a PR against the declared base, and obtain review;
9. wait for required checks and merge;
10. deploy and capture target-host evidence when applicable;
11. archive proof and residual risks;
12. move to done only after the reviewer verifies every acceptance item.

If current code invalidates a task assumption, the owner records a blocker with
evidence and proposes a scoped task update; it does not silently narrow the
product outcome.

## 12. Evidence Archive Layout

Program evidence belongs under:

```text
docs/deployment/evidence/loop-product-level/<task-id>/
```

Each directory should contain a redacted `README.md` or `evidence.json` index
linking the stored test, deployment, curl, browser, restart, security, and
review artifacts. Raw tokens, credentials, personal data, and large transient
test output must not be committed.

The final program closeout belongs under:

```text
docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/closeout/
```

## 13. Exit Criteria

The program is complete only when all of the following are true:

1. all primary `LOOP-PROD-*` tasks are terminal completed, not superseded to a
   weaker outcome;
2. all referenced external dependency tasks required by the DAG are completed
   with their own acceptance evidence;
3. the canonical inventory includes the twelve L1 loops and the separately
   classified `per_persona_ooda` loop;
4. every required worker has an authoritative deployment owner and default
   activation appropriate to its trigger model;
5. every command path has a real terminal side effect/readback or explicit
   terminal failure;
6. all twelve canonical loop-health rows plus the Per-Persona OODA overlay
   have current controller records and none are served as registry-only truth;
7. each loop reaches its task-declared target maturity, and every
   `product-level` claim has target-host proof;
8. Scenarios A through D pass from a clean target-host baseline;
9. frontend deployment is gate-controlled, paired with the exact BFF identity,
   rollback-safe on both sides, safe-write by default, and contains no reusable
   browser credential; the complete read-route and privileged-negative matrix
   passes under one coordinated cutover lease;
10. PPL, TJ, PINT, and Management AI closeouts pass their hosted acceptance;
11. duplicate, restart, failure, degraded, rollback, security, tenant, and
    no-live-capital assertions pass; admitted commands show RPO zero and
    recovery within two test controller intervals;
12. exact deployed SHAs, canonical task/run/worktree/scope admission, PRs,
    checks, formal review by a distinct admitted fleet runtime identity,
    evidence, reviewer verdicts, and residual risks are archived, with no
    planner-authored product artifact or duplicate semantic repair;
13. the final independent Human/Ops review accepts the evidence without an
    unresolved blocking risk.

Until all thirteen conditions are proved, this document remains an archived
planning baseline for active execution, not a completion declaration.

## 14. Additive Closeout Authority

The execution-time audit linked above found safety and evidence gaps that were
not represented by the original 36-task DAG. The additive packet raises the
catalog to 48 tasks. It preserves existing record fields while applying two
versioned, exact-preimage dependency patches to pristine baseline `todo` tasks
in the same atomic write that creates the twelve additive tasks. The
non-pristine live `LOOP-PROD-AUTH-001` record is not mutated;
`LOOP-PROD-BROWSER-AUTH-001`
depends on auth bootstrap, BFF auth, credential-free FE, credential lifecycle,
delivery provenance, and the environment lease, and is the sole coordinated
browser activation authority. `LOOP-PROD-DELIVERY-001` enforces that the
planner plans, dispatches, monitors, and reviews while supervisor-admitted
fleets implement and a distinct admitted runtime identity formally reviews
product artifacts.
`LOOP-PROD-CLOSE-001` is retained as the original baseline checkpoint;
`LOOP-PROD-SIGNOFF-001` installs protected Human/Ops transition enforcement;
`LOOP-PROD-CLOSE-002` is the sole final completion authority for this program.
