# Pantheon Current Full GAP, Dead-Code, and Duplicate-Mechanism Audit

Audit time: 2026-09-03 UTC

Code baseline: `ajoe734/pantheon` `origin/dev@675a488d78e8f991e2f1ecfc92e595b2d84625a1`

Frontend baseline observed from GitHub: `ajoe734/execute-plans`
`dev@4fd6088d5478d32911029628d5047b5e37c6bbdf`

Audit mode: read-only product/runtime inspection. This report does not deploy,
change runtime state, enable providers, open ingress, or authorize capital
activity.

## 1. Executive verdict

Pantheon has substantial implemented product code, and the five 2026-09-02
pre-shutdown code streams have now been integrated and independently verified.
The current `dev` branch is materially cleaner than the 2026-08-29 audit
baseline: the old `read_store.py` file is deleted, BFF route decorators have
been moved out of `main.py`, unsafe default egress and tunnel behavior is
guarded, SQLite family recovery is implemented, telemetry replay authorization
is explicit, and the development runtime is reconstructable.

The system nevertheless cannot currently be accepted as operationally closed:

- there is no accepted current hosted FE/BFF identity;
- the latest integration acceptance records `hosted_backend_sha=null`,
  `hosted_frontend_sha=null`, and hosted status `HELD CLOSED / PENDING`;
- repository variables now expose part of the dev target identity, but the
  authoritative environment document and an accepted hosted release still do
  not agree on one usable current pair;
- the latest exact-pair deployment for the audited SHAs failed before any
  switch because the hosted frontend manifest could not provide a parseable
  rollback baseline;
- the checked-in 12-loop integration suite executes only 2 component/static
  cases in this environment and skips 25 deployed cases;
- historic hosted acceptance is bound to the retired
  `35.201.204.12.sslip.io` environment and is not current proof;
- Management and Agora have broad source-level implementations but lack a
  current authenticated hosted journey against an admitted exact pair; and
- several residual compatibility paths, copied implementation blocks, missing
  port methods, process-local overlays, and owner ambiguities remain even after
  deletion of the monolithic read-store implementation.

### 1.1 Overall status

| Area | Code/contract | Local/static evidence | Current hosted evidence | Verdict |
|---|---|---|---|---|
| Twelve loops | broad implementation | partial | absent | **not 12/12 closed** |
| Management Console | routes/read models/actions present | partial | absent | **partial / hosted unverified** |
| Management AI | NL route and UI contract present | partial | absent | **partial / hosted unverified** |
| Agora | workshop, interaction, research, decision and performance modules present | partial | absent | **partial; critical journey unverified** |
| BFF decomposition | domain routers mounted; legacy store file deleted | 36 architecture tests passed, 1 skipped | not redeployed | **code accepted; hosted re-proof open** |
| Delivery | release tooling exists | GitHub branch CI green | exact served pair unknown | **not operationally accepted** |
| Development supervisor | reconstruction code accepted | focused evidence exists | host acceptance incomplete | **mechanism restored; runtime truth separate** |

No current evidence supports describing all twelve loops, Management, or Agora
as fully operational.

## 2. Evidence hierarchy and conflicts

This audit uses the following precedence:

1. exact current served identity and same-pair hosted acceptance;
2. current `origin/dev` code and current GitHub CI/PR state;
3. current isolated/component execution;
4. historical hosted evidence;
5. plans, task statuses, and static catalog declarations.

This ordering matters because the repository contains mutually stale facts.
The 2026-08-28 functional-closeout report says six hosted gates passed, but its
FE and BFF URLs point at the retired `35.201.204.12.sslip.io` environment. The
2026-09-03 pre-shutdown integration acceptance is newer and explicitly leaves
both hosted SHAs unknown. Historical success is retained as regression
evidence, not promoted into present-tense availability.

The checked-in `ai-status.json` on `origin/dev` is timestamped
`2026-07-12T21:15:39Z`; the pre-shutdown audit explicitly excludes it as
current task truth. It is not used to infer present completion.

## 3. Current delivery and environment gaps

### GAP-ENV-01 — no current exact hosted pair

The accepted integration manifest has no hosted backend SHA, frontend SHA, or
served identity. A green `dev` Branch CI or frontend Publish Promote run does
not establish what a VM currently serves.

Closure requires a release manifest binding the exact Pantheon SHA, exact
execute-plans SHA, image/artifact digests, compatibility digest, workflow run,
and post-switch `/deployment.json` plus `/bff/version` readback.

### GAP-ENV-02 — deployment target identity is only partially reconcilable

The second independent pass found that the repository-variable inventory had
changed during the audit: it now includes `DEV_BFF_PUBLIC_HOST` in addition to
the CORS/auth variables. The latest deploy job also resolved an SSH target and
FE/BFF URLs, so the earlier claim that all such values were absent was too
broad and is withdrawn.

The remaining gap is reconciliation: the merged `origin/dev` environment
document is stale, the accepted integration manifest has null hosted SHAs, and
the resolved job target has no successful exact-pair admission. Secrets were
not read or inferred. Closure requires one merged target identity plus a
successful manifest-bound deploy and served readback.

### GAP-ENV-03 — environment documentation is inconsistent

`origin/dev` still documents the retired `pantheon-lupin-dev-20260719`
environment as its dev baseline. A separate unmerged operational branch was
observed updating that truth to a new VM, but unmerged working-tree content is
not release truth. The authoritative environment document on `dev` must be
updated through the repository workflow before any operator relies on it.

### GAP-ENV-04 — no current frontend ingress acceptance

No current Pantheon-owned FE hostname, HTTPS listener, CORS-bound BFF origin,
or authenticated browser evidence is bound to the accepted `dev` head. This
blocks Management and Agora hosted acceptance even if their source tests pass.

### GAP-ENV-05 — no rollback baseline for the current pair

Without an admitted current pair, there is no current rollback target whose FE
artifact, BFF image, manifest, database compatibility, and served identity have
all been re-observed.

### GAP-ENV-06 — staging and production remain unavailable

The environment plan describes staging and production as future or
unavailable. Nothing in this audit authorizes interpreting dev code, historical
paper evidence, or publish tags as staging/production readiness.

## 4. Twelve-loop audit

The registry contains exactly twelve stable loop identities. A registry entry
is an inventory contract, not runtime proof.

| # | Loop | Current implementation found | Remaining GAP | Status |
|---:|---|---|---|---|
| 1 | Source Ingestion | controller, schedule/manual boundaries, SourceRecord, recovery and freshness policies | current one-shot stimulus -> durable record -> automatic reconcile-only readback on exact hosted pair | `PARTIAL` |
| 2 | Strategy Distillation | commit admission, durable queue, catch-up and distillation worker | same Loop-1 record ID must enter the queue naturally and produce terminal plus next-consumer receipts | `PARTIAL` |
| 3 | Alpha Replication | reviewed admission, ExperimentTask/Run and controller observation | current review command -> terminal ExperimentRun -> downstream receipt with one correlation chain | `PARTIAL` |
| 4 | Persona Teaching | session/events, preview/evaluation worker, persona target and consultation handoff | current user command -> evaluation -> target -> optional consultation readback | `PARTIAL` |
| 5 | Agora Interaction Evidence | durable request/outbox, worker, workshop and dataset handoff; real provenance now requires a real receipt | current terminal interaction, research continuation, SSE/reload and same-ID dataset handoff | `PARTIAL; HOSTED OPEN` |
| 6 | Human Imitation / Shadow Evaluation | handoff scheduler, candidate and Research intake | deployed scheduler -> candidate -> ExperimentRun, with legacy discovery path proven unreachable or removed | `PARTIAL` |
| 7 | Consultation | request, memo, executor/provider and Governance sink | real contribution -> governance receipt -> durable reload on exact pair | `PARTIAL` |
| 8 | Promotion / Deployment | approval, plan/outbox, registry/runtime authority and executable-binding implementation | normal release must naturally emit and verify loader plus market-data projection | `PARTIAL; HOSTED OPEN` |
| 9 | Capital Pool Execution | artifact-required producer, paper fleet, order/fill/position/heartbeat model | current executable binding -> paper signal -> order/fill/position/heartbeat with safe-write defaults | `PARTIAL; HOSTED OPEN` |
| 10 | Telemetry / Reconciliation | ingest/consumer, drift reconciler and IncidentCase | real Loop-9 fill/heartbeat -> drift/incident/recovery with identical IDs | `PARTIAL` |
| 11 | Evolution | threshold/daily producer, postmortem/evolution/dispatch workers | incident -> postmortem -> evolution decision -> dispatch receipt -> next consumer | `PARTIAL` |
| 12 | BFF Health Monitoring / Loop Truth | canonical 12-row projection and functional observations | authenticated Management same-ID display from current runtime owners, not static catalog maturity | `PARTIAL` |

### 4.1 What the current tests actually prove

Command executed against `origin/dev@675a488d7`:

```text
python -m pytest -q \
  tests/integration/l12/test_current_research_loops_deployed_e2e.py \
  tests/integration/l12/test_current_human_learning_deployed_e2e.py \
  tests/integration/l12/test_current_runtime_loops_deployed_e2e.py \
  tests/integration/l12/test_current_cross_loop_deployed_e2e.py
```

Result: `2 passed, 25 skipped`.

The skipped deployed cases cannot be counted as passing. The two executed
cases show useful component/static behavior only. They do not drive all twelve
loops against a current VM.

### 4.2 Required twelve-loop closeout evidence

Every loop must record, for one new correlated run:

- input stimulus ID;
- owner service and exact image/source identity;
- terminal output ID and terminal status;
- next-consumer receipt ID;
- durable fresh-reader/reload readback;
- retry, idempotency, DLQ and failure semantics;
- tenant/auth boundary evidence; and
- proof that no fixture, seed, prebuilt manifest ID, route mock, or direct store
  injection substituted for the owner path.

The final verifier must prove all twelve chains from the same release pair; it
must not concatenate unrelated historical receipts.

## 5. Management Console and Management AI gaps

### GAP-MGMT-01 — no current authenticated desktop acceptance

Routes and read-model code exist, but there is no current hosted login, shell,
navigation, DOM, network-error, empty/degraded-state, and reload sweep bound to
the current FE/BFF pair.

### GAP-MGMT-02 — current loop truth is not proven

The UI can only be as authoritative as its BFF projection. The required proof
is twelve canonical rows backed by current owner observations, plus separate
overlays. Static registry maturity or incident-derived reconstruction must not
be presented as live controller truth.

### GAP-MGMT-03 — unified port facade is valid, but its wiring is incomplete

Although the old `read_store.py` implementation is deleted, the global variable
is still named `read_store`. Its actual type is `ReadSurfacePorts`, a composite
of six narrower domain ports. Calling the facade `read_store` is legacy naming,
not by itself evidence of a second database.

The concrete wiring is nevertheless incomplete. Dynamic inspection of the
production factory found Capital, Deployment, Runtime, Ranking and Evolution
read ports `unavailable` because their stores/readers are not supplied. Only
the Persona registry store is explicitly injected at composition time. The
factory must bind each production port to its real owner or intentionally
return a typed unavailable surface.

### GAP-MGMT-03A — mounted production routes call methods absent from the facade

`ReadSurfacePorts` does not define or dynamically supply several methods that
mounted production code calls, including:

- `create_runtime_binding`;
- `create_deployment_plan` (through `service.read_store`);
- `update_persona`;
- `create_experiment_bff`;
- `list_experiments_bff`;
- `get_experiment_artifacts`, `get_experiment_logs`, and
  `get_experiment_metrics`;
- `get_job_logs_bff`;
- `create_research_ticket`;
- `record_agora_audit_event`; and
- `record_sponsor_decision`.

The deprecated ranking-formula create/patch code also refers to missing
methods, but it is unreachable after an unconditional deprecation return and
is dead code rather than an active route failure. The mounted/runtime and
Persona call sites require route-level tests proving they cannot raise
`AttributeError` on the production factory.

### GAP-MGMT-04 — command adapter legacy path remains

`command_executor.py` still defines `_execute_bff_action_adapter`, and tests
explicitly exercise a deprecated legacy receipt path. The central command
executor is valid; the legacy adapter path needs a caller inventory proving it
is either unreachable in production or deliberately retained with a dated
removal contract.

### GAP-MGMT-05 — process-local overlays remain beside canonical owners

Confirmed process-local state includes `_PERSONA_BFF_OVERLAY`,
`_STRATEGY_BFF_OVERLAY`, `_GOV_BFF_INCIDENT_OVERLAY`, `_GOV_BFF_JOB_OVERLAY`
and the `ReadSurfacePorts._ranking_snapshots` fallback. Persona overlay state is
defined separately in both `main.py` and `personas/service.py`. These may be
compatibility projections, but they are not durable and can diverge between
replicas or disappear on restart. Every production write must return/read from
the canonical owner; overlays may only be derived caches with explicit
invalidation and provenance.

### GAP-MGMT-06 — Management AI provider/action chain is unverified

The NL route and frontend action contract exist. Current evidence does not show
OpenClaw answer/SSE -> confirmed paper-only domain action -> terminal command
receipt -> durable reload on the current pair. Provider readiness must remain
separate from login readiness and must degrade honestly.

### GAP-MGMT-07 — stale task projection can mislead operators

The checked-in `ai-status.json` is stale and must not be used by product UI or
this report as current canonical development-task truth. Development task
authority belongs to the V2 TaskStore and local supervisor tooling, not the
product BFF.

### GAP-MGMT-08 — frontend production reachability requires re-audit

The frontend source is a separate repository. GitHub establishes its current
`dev` SHA and successful publish workflows, but this Pantheon-only worktree
cannot prove zero production reachability to frontend seed, mock, fallback, or
write-overlay modules. The exact execute-plans candidate needs its bundle
dependency graph and strict-live tests attached to hosted acceptance.

## 6. Agora gaps

### GAP-AGORA-01 — complete product journey is not current-proofed

The required chain is:

```text
Workshop message
  -> durable reconstruction
  -> interaction worker terminal result
  -> authentic research execution
  -> candidate pool
  -> Trading Room decision
  -> policy/consultation handoff
  -> performance attribution/suggestion
  -> same-ID reload
```

No current exact-pair evidence completes this chain.

### GAP-AGORA-02 — research provenance code is fixed; hosted proof remains

The current `DefaultAllowlistedAdapter` sets `real` only when the requested mode
is real and a real backend receipt exists. A real request without the receipt
is downgraded to `simulation`; unknown provenance becomes `unavailable`. The
earlier fake-real code defect is therefore closed in source. Hosted acceptance
must still prove that callers cannot forge `has_real_receipt` and that the
receipt resolves to the authentic backend execution.

### GAP-AGORA-03 — suggestion producer natural caller is unproven

`PerformanceSuggestionProducer` exists as production code, but the source
inventory performed here did not find a clear non-test instantiation/caller
outside its module exports. A paper telemetry or evaluation consumer must
naturally invoke it and persist a suggestion that the UI reads back.

### GAP-AGORA-04 — Decision Journal has two implementations, one apparently orphaned

Two journal implementations remain in the source tree:

- Governance `DecisionJournalStores` with `create_entry` and `patch_entry`;
- Agora service `create_journal_entry` and `patch_journal_entry`, backed via
  the injected read-store contract.

The current `/bff/agora/journal` route calls the Agora service implementation.
The production caller search found no non-test caller of Governance
`create_entry`/`patch_entry` or `DecisionJournalStores`. This is not evidence of
active dual-write; it is evidence of a second, apparently orphaned durable
implementation. Architecture must either wire Governance as the canonical
owner and adapt Agora to it, or formally retain Agora and delete the orphaned
Governance implementation. Keeping both invites a future split-brain writer.

### GAP-AGORA-05 — interaction worker hosted lifecycle is unverified

The worker has queue/lease/retry/outbox code. Missing current proof includes
worker identity, health, claim/ack, retry/DLQ, terminal response, SSE reconnect,
and reload from a fresh reader.

### GAP-AGORA-06 — candidate decision and learning handoffs are unverified

Candidate pool and decision modules exist, but a normal hosted UI decision has
not been shown to produce both its canonical DecisionEvent and exactly one
policy/consultation handoff.

### GAP-AGORA-07 — compatibility and current FE/BFF pairing are unverified

Contract manifests and compatibility tooling exist. No current served
deployment binds the current Agora frontend bundle to the exact BFF contract
hashes and advertised capability set.

## 7. Dead-code and duplicate-mechanism audit

### 7.1 Confirmed removals / no longer duplicate

| Item | Observation | Verdict |
|---|---|---|
| legacy `services/control-plane/bff/read_store.py` | file absent; deletion guard exists | removed |
| generic `action_adapter.py` / `generic_action_adapter.py` files | absent | removed |
| inline FastAPI route ownership in `main.py` | zero top-level `@app.get/post/put/patch/delete` decorators | removed from composition root |
| route shadowing/uniqueness | focused architecture suite passed | no currently detected normalized duplicate route |
| unsafe default Yahoo/Anue scheduling and public tunnel | code streams merged and independently verified | code gap closed; hosted startup re-proof required |
| second task-state genesis source | genesis guard refuses existing truth; stale `ai-status.json` is not canonical | duplicate authority rejected by design |

### 7.2 Confirmed residual duplication or legacy debt

| ID | Mechanism | Evidence | Risk | Required disposition |
|---|---|---|---|---|
| DUP-01 | Agora vs Governance Decision Journal implementations | both implementations remain; only Agora has a current production caller | orphan code can later become a split writer | select one owner; adapter-only compatibility; delete the unused implementation |
| DUP-02 | copied BFF implementation blocks | AST comparison found 208 exact cross-file definition groups; `main.py` and `personas/service.py` share 163 top-level names | fixes can land in one copy while runtime uses another | reduce to imports/injected dependencies and one definition per responsibility |
| DUP-03 | central command service vs deprecated `_execute_bff_action_adapter` | legacy helper and receipt tests remain | commands may bypass typed domain adapters | prove zero production callers or schedule deletion |
| DUP-04 | product environment truth in docs vs operational branch | `dev` document names retired environment while unmerged work records a replacement | operators and automation can target different hosts | merge one authoritative environment identity and remove obsolete current-tense claims |
| DUP-05 | process-local overlays beside durable owners | Persona, Strategy, Incident, Job and Ranking snapshot overlays remain | replica/restart divergence and false readback | eliminate write authority; retain only explicit derived caches where required |

### 7.3 Suspected duplication requiring targeted verification

| ID | Suspected overlap | Why it is not yet called confirmed duplication | Required test |
|---|---|---|---|
| SUS-01 | Persona lifecycle facade and persistent owner | current production facade lacks the called mutation method, while overlay writes remain | trace create/update/lifecycle transition to one table and one state machine |
| SUS-02 | RuntimeBinding BFF create route and Runtime Manager service | current facade lacks `create_runtime_binding`; the route may fail before reaching the owner | reject non-admissible local payload and prove owner readback |
| SUS-03 | DeploymentPlan local route and Deployment service | the service calls a facade method while the current facade has no matching method | one command, one outbox event, one durable plan ID |
| SUS-04 | Ranking formula/snapshot/evaluation storage | historical generic ranking record could not round-trip all shapes | schema/table inventory plus lossless fresh-reader tests |
| SUS-05 | incident-derived loop reconstruction and canonical Loop Truth | reconstruction may be legitimate backfill | UI/API provenance must label backfill and never override live truth |
| SUS-06 | seed/fixture modules vs production data paths | seed code is legitimate for explicit local/CI use | production bundle/import and Compose startup reachability must be zero by default |
| SUS-07 | multiple schedulers/workers | distinct loop owners are legitimate; duplicates only if they consume the same queue/lease | enumerate subject/queue, lease key and singleton owner for every Compose worker |

### 7.4 Large-file and boundary debt

`services/control-plane/bff/main.py` is reduced from the former 68k-line
application, but remains 22,946 lines with 1,077 top-level AST nodes. It mounts
37 router groups and has zero inline route decorators. It still contains 678
top-level class/function definitions. An AST-normalized scan found 208 exact
definition groups copied across production files. The largest overlap is
between `main.py` and `personas/service.py`: 163 shared top-level names, of
which 158 have AST-identical definitions,
including auth, error, surface-status, Persona lifecycle, projection and
Management helpers. Not every small utility duplicate is harmful, but this
volume demonstrates that the router extraction copied large implementation
blocks instead of completing ownership removal from the composition root.

One production reverse dependency remains:

```text
services/control-plane/bff/deployment/service.py
  -> from main import _surface_degradation_reason
```

That helper should move to a neutral shared module or be injected. Domain
services importing the composition root weakens ownership boundaries and makes
isolated testing/import order fragile.

### 7.5 Stale tests and references

Several tests still directly access `bff_main.read_store` or mention the
deleted `read_store.py`. The global object is now a port facade, so the name is
not proof of a deleted store being instantiated. The references still create
two risks:

- tests may validate an obsolete facade instead of the canonical owner; and
- future developers may recreate the deleted implementation to satisfy stale
  tests.

Each such test should be classified as owner-contract test, compatibility test
with an expiry, or deleted/rebased onto domain fixtures.

### 7.5A Confirmed unreachable implementation tails

A second AST control-flow scan found 17 function bodies containing statements
after an unconditional top-level `return` or `raise`. These are not merely
unused symbols; their trailing bodies cannot execute:

- eight compatibility functions in `management_ai_store.py` (`get_session`,
  `list_sessions`, `upsert_session`, `append_turn`, `list_turns`,
  `upsert_assistant_session`, `get_assistant_session`, `find_attachment`);
- one deprecated action tail each in Deployment, Personas, Strategies and
  Runtime routers; and
- five deprecated Ranking formula/list/action tails.

Delete the tails once their deprecation responses are retained in minimal
handlers. Until deletion, they inflate caller searches and can make dead
fallback logic look like a second active implementation.

### 7.5B Apparent duplicate routes are composition-gated aliases

The literal route scan found three paths declared in two router modules:
`GET /bff/sse/deployment/events`, `GET /bff/sse/agora/signals`, and
`GET /bff/sse/agora/sessions/{sessionId}`. These are not active collisions in
the current app: the generic events router is mounted with
`include_domain_sse_aliases=False`, and the normalized route-uniqueness suite
passes. They are source-level compatibility declarations behind mutually
exclusive composition, so this audit does **not** classify them as duplicate
runtime mechanisms.

### 7.6 Legitimate multiple implementations that are not duplication

The following should not be removed merely because several adapters exist:

- broker adapters for IBKR, Shioaji, Kraken and sandbox/paper execution;
- research engine adapters such as vectorbt, RLlib, Qlib, FinRL and
  statsmodels;
- source connectors for distinct licensed/public providers;
- Agora trading-data widget adapters for different read models; and
- command adapters for different domain commands.

They implement separate provider/domain contracts. They become architectural
duplication only if two of them claim the same authoritative state, queue,
route, scheduler lease, or write table without an explicit selection policy.

### 7.7 Maintainability and module-structure audit

The additional structure pass found systemic coupling beyond duplicate
definitions:

- `main.py` is 22,946 lines and `personas/service.py` is 13,985 lines;
- 218 test modules import `main` directly, making the composition root a test
  fixture and encouraging global monkeypatching;
- the BFF tree contains 323 `sys.path.insert`/`append` operations across
  production and tests; 18 remain in production-classified files;
- production code contains eight `globals()` calls, including dynamic service
  lookup and symbol forwarding;
- `personas/router.py` forwards service symbols through `globals()[name]`, so
  the apparent module boundary is not an ownership boundary; and
- router factory functions reach 3,383 lines (Personas), 2,718 (Research),
  1,970 (Agora Trading Room), 1,936 (Agora Research), and 1,522 (Strategies).

These patterns explain the route-test timeouts and the 208 duplicate-definition
groups: imports, dependency construction, handlers, application behavior,
projection and compatibility logic remain coupled at module load or inside
giant router closures. The structural remedy is explicit domain packages and
constructor/router-factory injection, accompanied by caller migration and
deletion. Merely splitting these files or adding forwarding modules would not
close this gap.

## 8. Code and CI verification performed

### 8.1 BFF architecture suite

Executed:

```text
python -m pytest -q \
  services/control-plane/bff/tests/test_bff_main_composition.py \
  services/control-plane/bff/test_architecture_boundaries.py \
  services/control-plane/bff/tests/test_read_store_final_deletion.py \
  services/control-plane/bff/test_normalized_route_uniqueness.py \
  services/control-plane/bff/test_route_resolution_no_shadowing.py \
  services/control-plane/bff/test_no_undefined_call_symbols.py
```

Result: `36 passed, 1 skipped`; two `jsonschema.RefResolver` deprecation
warnings were emitted.

### 8.2 Compose inventory

`docker compose config --services` resolves 52 services. Relevant owners and
workers include operator BFF, source ingestion/controller/scheduler,
distillation worker, training service/worker, Agora interaction worker,
policy-learning scheduler, consultation, deployment consumer, Runtime Manager,
paper signal/fleet, telemetry/reconciliation and Evolution workers.

This proves that service definitions compose. It does not prove containers are
currently deployed, healthy, singleton, authenticated, or attached to durable
dependencies.

### 8.3 GitHub evidence observed

- Pantheon `dev@675a488d7`: Branch CI Gate run `33753854113` succeeded.
- execute-plans `dev@4fd6088d5`: recent Publish Promote workflows succeeded.
- the latest exact-pair dev release run `33764725958`, for Pantheon
  `675a488d7` plus execute-plans `4fd6088d5`, failed before switching. Its
  `Capture exact hosted FE and BFF rollback baseline` step downloaded content
  that failed JSON parsing at the frontend manifest boundary; every deploy,
  hosted probe and admit/switch step after it was skipped. Therefore this run
  proves fail-closed release behavior, not a deployed product.
- a promotion candidate on Pantheon (`promote/v2026.09.03.1`) had Stage 0,
  Regression, Research Regression, BFF Route Diff and Canonical Review failures.
- several old implementation PRs remain open even though equivalent verified
  changes have subsequently merged through governed task PRs; these stale PRs
  should be closed to reduce delivery ambiguity.

CI and publish results are code/release evidence, not current hosted identity.

### 8.4 Route-level execution verification limitation

The focused architecture suite completed, but attempts to execute the broader
route-level files covering the newly separated domains did not reach a pytest
summary within the bounded two-minute combined run; bounded individual runs
also timed out after emitting only progress dots. They are recorded as
`NOT COMPLETED (timeout)`, never as passes. This is both an evidence gap for the
missing-facade-method findings and a test-isolation/performance maintenance
gap.

### 8.5 Two additional independent audit passes

After the initial report, two full passes were performed independently and
then cross-compared:

| Pass | Independent lens | Reproduced / corrected result |
|---|---|---|
| Pass A | production AST, route declarations, call sites, factory wiring, Compose ownership | reproduced facade under-wiring, process-local overlays and large copied blocks; added 17 unreachable tails; rejected three gated SSE aliases as active duplicates |
| Pass B | current GitHub runs/deployments/variables, acceptance artifacts, skip/timeout semantics | corrected the over-broad missing-variable statement; confirmed latest exact-pair deploy failed before switch and current hosted acceptance remains absent |

Cross-pass invariant: neither pass found evidence that all twelve loops, the
Management system, or Agora are operationally closed on one current exact
release pair. Findings based only on symbol names were downgraded to
`suspected`; active duplication requires a caller/writer/route-owner proof.

## 9. Consolidated GAP register

| Priority | GAPs | Exit condition |
|---|---|---|
| P0 | ENV-01, ENV-02, ENV-04, ENV-05 | current exact FE/BFF pair deployed, identified, authenticated, rollback-safe |
| P0 | mounted calls to absent `ReadSurfacePorts` methods | route-level execution reaches one canonical owner without `AttributeError` |
| P0 | Loop 5, 8, 9 hosted chains | authentic Agora research, executable binding and paper lifecycle receipts |
| P0 | MGMT-01, MGMT-02, MGMT-06 | authenticated Management/AI journey and twelve current owner rows |
| P0 | AGORA-01, AGORA-02, AGORA-03 | complete workshop-to-performance chain with real provenance and natural suggestion caller |
| P1 | DUP-01, DUP-02, DUP-03, DUP-05 | single implementation/writer per responsibility; legacy callers zero or explicitly expiring |
| P1 | twelve-loop same-run proof | stimulus/terminal/next-consumer/fresh-reader identity for all 12 |
| P1 | ENV-03 | one merged authoritative environment document |
| P1 | main reverse import | zero production imports from composition root |
| P1 | module/test architecture coupling | zero dynamic symbol forwarding; bounded router factories; tests target domain contracts instead of patching `main` |
| P1 | execute-plans mock reachability | zero strict-live reachability to mock/seed/write overlay |
| P2 | stale tests/references and stale PRs | obsolete facades, references and superseded PRs retired |
| P2 | deprecation warnings | migrate away from deprecated `jsonschema.RefResolver` |

## 10. Recommended closure sequence

1. Merge one authoritative current environment identity and configure the
   missing deployment target variables.
2. Close superseded open PRs so only live delivery lines remain visible.
3. Repair and test every mounted call to a method absent from
   `ReadSurfacePorts`.
4. Remove the deployment-service reverse import from `main`.
5. Deduplicate the 163-name `main.py` / `personas/service.py` overlap and move
   shared helpers to neutral modules rather than retaining copied bodies.
6. Produce a mutation-to-owner/store/table inventory and remove process-local
   write authority from overlays.
7. Resolve Decision Journal ownership and remove the orphan implementation.
8. Prove or remove the deprecated command action-adapter path.
9. Audit execute-plans strict-live bundle reachability at the current frontend
   SHA.
10. Build and admit one exact FE/BFF candidate with safe writes and no
   unauthorized egress.
11. Execute the three loop families—research 1-4, human learning 5-7, runtime
   8-12—without skipped mandatory cases.
12. Execute a new cross-loop stimulus and same-ID verifier across all twelve.
13. Run authenticated Management, Management AI and Agora hosted journeys.
14. Perform rollback and re-observe the served manifest before declaring
    closure.

## 11. Final acceptance statement

The current source line is significantly more complete and structurally safer
than the prior audit baseline. Five major pre-shutdown code gaps are accepted,
the old monolithic read-store file is gone, inline BFF route ownership is gone,
the fake-real Agora provenance defect is fixed in code, and focused
architecture checks are green. Those checks did not detect the copied helper
blocks or mounted calls to methods absent from the new facade; both are current
source defects that require explicit follow-up.

The remaining barrier is not merely documentation. Current deployment identity,
hosted execution, same-run twelve-loop evidence, Management/Agora authenticated
journeys, and several single-writer proofs are missing. Until those artifacts
exist for one exact current release pair, the correct state remains:

> **Code substantially implemented; 12/12 operational closure not proven;
> Management and Agora partial; hosted acceptance held closed.**
