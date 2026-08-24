# Pantheon External Data Source Management SA — 2026-08-24

Date: 2026-08-24

Input: [`CURRENT_GAP_2026-08-24.md`](CURRENT_GAP_2026-08-24.md)

Goal: complete phase-1 external data coverage and make every supported source
independently manageable from the Management product, while preserving bounded
egress, evidence lineage, explicit operator authority, and the separation
between product runtime and development tooling.

OpenClaw-assisted source discovery and connector development are phase 2 and
are not prerequisites for this architecture.

## 1. Scope and outcome

### 1.1 Phase-1 outcome

An authorized operator can use `/management/data-sources` to:

- see every deployed connector definition and configured source instance;
- distinguish supported, configured, credential-ready, validated,
  canary-passed, enabled, fresh, degraded, disabled, and retired states;
- add a source that uses an already deployed connector definition;
- attach a secret reference without reading the secret;
- validate configuration and policy;
- run a bounded read-only canary;
- enable, disable, degrade, resume, reschedule, replace, and retire a source;
- inspect runs, watermarks, freshness, schema drift, DLQ, quota, cost, usage,
  consumers, evidence, and action receipts; and
- verify that accepted source data is searchable and traceable into research,
  strategy seeds, experiments, and reviewed memory.

### 1.2 Explicit non-goals

Phase 1 does not:

- let OpenClaw or another LLM implement connector code;
- create a product-hosted development bridge;
- give the browser direct access to source-ingest or provider credentials;
- enable unrestricted external egress or full-market full-depth pulls;
- treat catalog templates, fixtures, or process health as live provider proof;
- write raw external data directly to memory;
- route orders or change capital state; or
- hard-delete source history or lineage.

## 2. Architecture decisions

### AD-SRCM-01 — Retain one source authority

`services/source_ingestion` remains the source owner. Do not add a second data
source service, registry, scheduler, or BFF-local source store.

The source owner stores canonical source-instance policy and reconciles runtime
configuration. Search, research, memory, BFF, and frontend consume published
contracts; they do not become source authority.

### AD-SRCM-02 — Separate definition, instance, desired state, and observation

The product model has four distinct layers:

| Layer | Owner | Meaning |
|---|---|---|
| `ConnectorDefinition` | code/build | deployed adapter capability and version |
| `DataSourceEntry` | source registry | operator-admitted provider/dataset instance and policy |
| `SourceDesiredState` | source controller | requested lifecycle, schedule, universe and config revision |
| `SourceObservedState` | source runtime | health, runs, watermark, freshness, errors and deployment identity |

Existing `ProviderAdapterSpec` and catalog config templates project the
code-owned `ConnectorDefinition`; they are not duplicated into a mutable
product registry. Existing connector config, schedule, health, usage, and audit
stores compose the desired/observed model.

### AD-SRCM-03 — New sources start disabled

Creating a supported source never starts provider egress. The initial state is
`configured_disabled`. Activation requires configuration validation and a
bounded canary using the exact deployed connector definition.

### AD-SRCM-04 — Every source mutation is a command with an effect receipt

Source commands are admitted by the BFF with role checks, explicit
confirmation where required, idempotency, expected revision, and audit
identity. The BFF calls source-ingest through a service-authenticated client.

A command is successful only when the source owner returns a durable effect
receipt and readback confirms the requested revision. A proposal status or HTTP
202 response alone is not success.

### AD-SRCM-05 — Desired and observed state never collapse

The requested lifecycle can be `enabled` while observed state is `degraded` or
`credential_unavailable`. Management shows both. Runtime failures do not
silently rewrite operator intent; policy-driven automatic containment records a
separate observed transition and receipt.

### AD-SRCM-06 — Disabled and retired have different semantics

- `disabled` is reversible. It rejects new manual and scheduled ingest while
  preserving config, evidence, and history.
- `retired` is terminal for the source-instance identity. Re-admission creates a
  new identity/version after dependency and retention checks.

There is no hard-delete action in the normal Management workflow.

### AD-SRCM-07 — Safe egress posture remains bounded

The default dev posture remains `PANTHEON_EXTERNAL_EGRESS=deny` and
`reconcile_only`. A canary or explicit refresh opens only the reviewed connector
and host allowlist, uses record/byte/time/rate limits, then terminates.

Phase 1 makes this posture visible and operable; it does not replace it with an
unbounded scheduler.

### AD-SRCM-08 — Search access filters precede every ranker

License, entitlement, environment, persona, workspace, role, sensitivity,
capital-pool and as-of filters run before keyword, full-text, vector, or
structured-alpha ranking. No fallback retriever may bypass them.

### AD-SRCM-09 — External alpha is structured evidence, not executable intent

External Alpha DB signals are versioned, point-in-time source records with a
structured rule/signal payload. They can produce evidence and research seeds,
but never direct orders, deployment, or capital binding.

### AD-SRCM-10 — Memory admission follows reviewed knowledge

Raw SourceRecord, EvidenceItem, or search results remain evidence. Memory
writeback is allowed only after an owning lifecycle produces a reviewed or
published research finding, experiment lesson, postmortem, or approved
evolution decision.

### AD-SRCM-11 — Management extends the existing page

Retain `/management/data-sources` as the single Data Source Management entry
point. Add detail, create, and command flows to it. Do not create a second page
with an independent source list or fixture truth.

### AD-SRCM-12 — OpenClaw remains phase 2

Phase 1 may return `adapter_not_supported` with a structured development need.
It does not dispatch a task or modify a repository. Phase 2 may consume this
record and create a governed local-development handoff without changing source
runtime authority.

## 3. Target architecture

### 3.1 Runtime and product topology

```text
execute-plans /management/data-sources
        |
        | BFF read + governed commands
        v
Pantheon operator BFF
  - authentication / RBAC / confirmation
  - idempotency / expected revision
  - source DTO composition
  - command receipt projection
        |
        | service-authenticated HTTP
        v
source-ingest (single source owner)
  +---------------- ConnectorDefinition projection (code/catalog)
  +---------------- DataSourceRegistry / StrategySeedSourceRegistry
  +---------------- desired connector config and schedule
  +---------------- lifecycle command transaction
  +---------------- bounded canary / ingest / frontier / DLQ
  +---------------- observed health / usage / watermark / audit
        |
        +--> SourceRecord / EvidenceBundle / KnowledgeObject
        |             |
        |             +--> governed search / structured alpha query
        |             +--> distillation / StrategySpecSeed
        |             +--> research / experiment
        |
        +--> reviewed finding --> memory writeback

provider egress
  allowed only for the exact connector, host, secret ref and bounded run
```

### 3.2 Development boundary

```text
phase-1 product result: adapter_not_supported
        |
        v
structured development need (read-only product artifact)

phase 2 only:
operator-approved export/local intake
        -> local development tooling
        -> clean worktree / tests / PR / deploy
        -> new ConnectorDefinition appears in deployed build
        -> phase-1 create-disabled/canary/enable workflow
```

The product BFF does not expose task packets, worktrees, repository mutation,
or supervisor commands.

## 4. Canonical source management model

### 4.1 ConnectorDefinition

A read-only build projection answering “what can this deployed version do?”

Required fields:

- `definition_id`, `adapter_token`, `adapter_version`;
- provider/source classes and supported datasets;
- supported auth, fetch and cursor modes;
- config schema and sensitive-field markers;
- default limits, allowlisted host patterns, rate-limit capability;
- required PIT fields and output schema versions;
- deployment SHA/image identity;
- definition state: `supported`, `disabled_by_build`, or `experimental`;
- reason and current adapter test manifest reference.

### 4.2 DataSourceEntry

The admitted source instance answering “which provider/dataset are we allowed to
use and for what?”

Required fields extend the existing registry contract:

- stable source-instance ID and source kind;
- `definition_id` and connector ID;
- provider/account/dataset identity;
- license, entitlement tags, allowed use and retention/deletion policy;
- market/universe/data classification;
- source-specific freshness SLA;
- dependency and consumer references;
- lifecycle revision and policy version;
- created/updated actor and timestamps.

### 4.3 SourceDesiredState

The operator/controller intent:

- lifecycle: configured-disabled, enabled, disabled, retired;
- schedule enabled/cadence/timezone/jitter;
- active-universe policy ref;
- connector config with public values and secret refs only;
- fetch/canary limits and allowed hosts;
- expected definition/deployment version;
- monotonically increasing revision;
- last command and receipt refs.

### 4.4 SourceObservedState

The latest source-owner observation:

- effective lifecycle and reconciliation status;
- credential readiness without secret values;
- config validation result;
- last canary and normal run;
- watermark, row counts, rejection counts and freshness;
- schema version/drift and search-index readback;
- DLQ/unresolved frontier counts;
- quota, cost, usage and yield;
- dependent consumers;
- deployed connector definition and service SHA;
- degraded/blocked reasons and last observed time.

## 5. Lifecycle architecture

### 5.1 State machine

```text
candidate
   |
   | CreateSupportedSource
   v
configured_disabled
   |
   | ValidateConfiguration
   v
validated_disabled
   |
   | RunBoundedCanary
   v
canary_passed_disabled
   |
   | EnableSource
   v
enabled ---------------------> degraded
   |                              |
   | DisableSource                | DisableSource / automatic containment
   v                              v
disabled <-------------------- degraded_disabled
   |
   | Revalidate + Canary + ResumeSource
   +------------------------------> enabled

configured_disabled / disabled / degraded_disabled
   |
   | RetireSource after dependency gate
   v
retired (terminal)
```

`validating` and `canary_running` are command execution states, not durable
desired lifecycle states. They appear in command receipts and observed state.

### 5.2 Preconditions

| Command | Preconditions |
|---|---|
| Create | deployed supported definition, valid policy, unique source ID |
| Validate | configured-disabled/disabled, expected revision matches |
| Canary | validation passed, credential ready if required, exact hosts/limits |
| Enable | current canary passed, freshness window valid, no blocking policy gap |
| Disable | source not retired; reason required |
| Resume | disabled, validation/canary still valid or rerun required |
| Schedule | not retired; interval and universe within definition/policy limits |
| Replace | replacement source validated; dependency migration plan present |
| Retire | disabled, no active blocking dependency, retention disposition present |

### 5.3 Failure behavior

- validation failure leaves desired state disabled;
- canary failure leaves desired state disabled and records a typed reason;
- credential failure exposes only `credential_unavailable` and a secret-ref ID;
- schema drift degrades or disables according to source policy;
- stale data never becomes fresh because the service process is healthy;
- search notification failure produces a partial canary result and cannot pass
  full downstream activation;
- retry uses the same idempotency key and does not create another source/run.

## 6. Management experience

### 6.1 Information architecture

The existing page becomes a Data Source Control Center with four views inside
the same route family:

1. **Instances** — current source table and filters;
2. **Catalog** — deployed connector definitions and unsupported required
   capabilities;
3. **Runs & health** — canary/ingest runs, watermarks, freshness, DLQ and quota;
4. **Change history** — immutable command/effect receipts and retired sources.

Phase-2 OpenClaw proposal/development views are not added in phase 1.

### 6.2 Instance table

Each row shows:

- source/provider/dataset/market;
- definition support and deployed version;
- desired lifecycle and observed health;
- credential, license and entitlement posture;
- schedule, watermark, freshness and latest row count;
- search-index and evidence readback;
- consumer personas/strategies;
- cost/quota/usage summary; and
- allowed actions derived by BFF policy.

Row actions use a menu rather than placing multiple destructive buttons in the
table. Every action opens a confirmation panel with current revision,
preconditions, effect, and rollback.

### 6.3 Add-source wizard

```text
select deployed definition
  -> provider/dataset identity
  -> public connection/config fields
  -> secret reference and required scopes
  -> license/allowed-use/retention
  -> universe and schedule (initially disabled)
  -> review
  -> create configured_disabled
  -> validate
  -> optional bounded canary
  -> explicit enable
```

If no deployed definition matches, the wizard stops at
`adapter_not_supported`, shows required capability fields, and offers export of
a development-need record. It does not claim the source was added.

### 6.4 Detail view

The detail drawer/page contains:

- identity and policy;
- desired versus observed state;
- configuration with secrets redacted;
- schedule and active-universe policy;
- current definition/deployment identity;
- run/watermark/freshness timeline;
- schema drift and error/DLQ history;
- evidence/search/seed/memory lineage;
- dependencies and retirement blockers; and
- command receipt history.

## 7. Provider completion strategy

Provider work is organized by capability, not by optimistic catalog status.

### Wave A — P0 canonical market and reference coverage

- reconcile existing TWSE/TPEx, MOPS, FinMind, SEC, FRED, FINRA, CoinGecko,
  Polygon, Alpha Vantage, IBKR and Shioaji definitions with actual adapters;
- implement TDCC and TAIFEX adapters;
- define security master, symbol map, calendar and corporate-action coverage;
- produce current-deployment bounded canary evidence per provider;
- keep paid providers disabled until entitlement and credential gates pass.

### Wave B — news and research corpus

- complete Yahoo/Anue/FinMind news metadata admission, dedup and rights;
- admit OpenAlex and allowlisted GitHub as managed strategy-seed sources;
- add arXiv/SSRN/PDF ingestion only after license/citation/retention rules;
- prove evidence and search readback.

### Wave C — social and external alpha

- introduce a dedicated social source class instead of projecting social as
  news;
- select at least one authorized social provider and define bot/moderation/
  deletion handling;
- implement vendor-neutral External Alpha DB contract and one real provider;
- prove point-in-time structured rule queries and immutable result snapshots.

### Wave D — alternative data

- admit providers only through an explicit value, cost, license, privacy,
  stability and reproducibility gate;
- no alternative provider is required to claim the control plane complete, but
  the family must remain honestly `not_configured` until one is selected.

## 8. Search and knowledge architecture

### 8.1 Retrieval modes

One governed gateway supports explicit modes:

- `keyword` — deterministic lexical baseline;
- `full_text` — durable field-aware text index;
- `semantic` — vector retrieval after access filtering;
- `hybrid` — calibrated lexical plus semantic ranking;
- `structured_alpha` — field/rule query against versioned alpha signal data.

The caller chooses or policy selects a supported mode. An unavailable mode
fails explicitly; it does not silently fall back and retain the requested label.

### 8.2 Query truth

Every query records:

- actor/persona/workspace/environment/purpose;
- source, license, role, sensitivity and capital-pool filters;
- event/as-of/available-time bounds;
- query/rule fingerprint;
- index and dataset versions;
- result snapshot identity and citations; and
- rejected-result counts by policy reason.

### 8.3 Downstream learning

```text
external evidence
  -> research or seed
  -> experiment/evaluation
  -> reviewed conclusion
  -> research_finding memory writeback
  -> later research retrieval with citation and counter-evidence
```

Memory preserves origin license, allowed use, supersession, confidence,
contradiction, expiry and deletion obligations.

## 9. Work packages

The labels below are architecture work packages, not canonical task IDs.

### SA-SRCM-01 — Canonical management contracts

Deliver:

- ConnectorDefinition projection;
- composed source-management DTO;
- desired/observed state and revision;
- lifecycle command and receipt schemas;
- canary-result schema;
- source coverage and terminology registry.

Closes: SRCM-G01, SRCM-G02, SRCM-G08, and part of SRCM-G20.

### SA-SRCM-02 — Source-owner command effects

Deliver:

- transactional create-disabled and lifecycle commands;
- validation and bounded canary;
- real effect/readback receipts;
- service authorization for mutations;
- schedule, replace and retire precondition enforcement;
- proposal apply semantics correction.

Closes: SRCM-G03, SRCM-G04, SRCM-G07, SRCM-G09, and SRCM-G18.

### SA-SRCM-03 — BFF management facade

Deliver:

- canonical list/detail read model;
- create, validate, canary, lifecycle, schedule, replace and retire commands;
- RBAC, confirmation, idempotency and revision checks;
- normalized allowed-actions and degraded states.

Closes: SRCM-G05 and part of SRCM-G06/SRCM-G07.

### SA-SRCM-04 — Management frontend control center

Deliver:

- expanded existing `/management/data-sources` route;
- catalog/instances/runs/history views;
- add-source wizard and detail view;
- command confirmations, pending state, receipts and reload readback;
- strict-live, no fixture-success behavior.

Closes: SRCM-G06, SRCM-G17, and part of SRCM-G18.

### SA-SRCM-05 — Provider coverage completion

Deliver:

- reconciled definition/adapter/catalog matrix;
- TDCC and TAIFEX;
- social and External Alpha DB real-provider paths;
- source-family gaps and current deployment canaries;
- license, credential, quota and freshness evidence.

Closes: SRCM-G10, SRCM-G11, SRCM-G13, and SRCM-G16.

### SA-SRCM-06 — Governed search and structured alpha

Deliver:

- executed time/as-of and missing mandatory filters;
- full-text/vector/hybrid capability with honest mode reporting;
- structured alpha schema/query/snapshot;
- query audit and citation readback.

Closes: SRCM-G12 and SRCM-G13.

### SA-SRCM-07 — Evidence-to-learning closure

Deliver:

- real provider SourceRecord-to-seed/research acceptance;
- reviewed research finding memory writer;
- license/supersession/expiry propagation;
- retrieval and influence evidence without constant inferred weights.

Closes: SRCM-G14, SRCM-G15, and SRCM-G16.

### SA-SRCM-08 — Hosted acceptance and documentation alignment

Deliver:

- exact FE/BFF deployment identity;
- write-enabled candidate only for bounded operator acceptance;
- hosted browser create-disabled/canary/enable/disable/reload journey;
- negative RBAC, stale revision, secret exposure and egress tests;
- corrected current docs and active frontend paths.

Closes: SRCM-G19, SRCM-G20, and the remaining acceptance gaps.

## 10. Dependency graph and sequence

```text
SA-SRCM-01 contracts
   |
   +--> SA-SRCM-02 source effects
   |         |
   |         +--> SA-SRCM-03 BFF facade
   |                   |
   |                   +--> SA-SRCM-04 Management UI
   |
   +--> SA-SRCM-05 provider coverage
   |
   +--> SA-SRCM-06 search/alpha
             |
             +--> SA-SRCM-07 evidence-to-learning

02 + 03 + 04 + 05 + 06 + 07
   -> SA-SRCM-08 hosted acceptance
```

Recommended delivery order:

1. contracts and truth vocabulary;
2. source-owner effect transactions and service auth;
3. BFF facade;
4. Management read/action workflow for existing supported providers;
5. missing P0 providers and current canaries;
6. structured alpha/search;
7. reviewed memory closure; and
8. exact-pair hosted acceptance and documentation alignment.

Provider work may run in parallel after contracts freeze, but no provider is
labelled complete before SA-SRCM-08 evidence.

## 11. Migration strategy

1. Add new DTOs and command schemas without changing existing read routes.
2. Project current catalog/adapter/config/schedule/health into the new read
   model and compare against existing Management rows.
3. Add source-owner commands behind a feature flag and keep all existing
   sources disabled from new UI actions.
4. Add BFF commands and tests; frontend remains `VITE_BFF_REAL_WRITES=false` in
   the normal served artifact.
5. Add Management views and dry-run/validation paths.
6. Run a bounded write-enabled candidate against a non-capital test source.
7. Reconcile current source instances and provider coverage.
8. Enable per-source commands for approved roles after exact hosted acceptance.
9. Retire inferred provider-status parsing only after canonical DTO parity.

Rollback always disables the new command feature and returns to the existing
read-only page. It never deletes source evidence or re-enables a provider.

## 12. Acceptance architecture

### 12.1 Contract acceptance

- definition, instance, desired and observed identity remain distinct;
- every write requires expected revision and idempotency key;
- all receipts identify actor, command, source, before/after revision, effect,
  readback and deployment SHA;
- no response includes a raw secret;
- disabled/retired semantics are deterministic.

### 12.2 Runtime acceptance

- one supported no-secret source completes create-disabled, validation, canary,
  enable, bounded run, disable and reload;
- one credentialed test source proves secret-ref resolution without exposure;
- an unauthorized actor and stale revision are rejected before mutation;
- egress outside the exact allowlist is rejected;
- duplicate commands return the same receipt/effect;
- search/evidence readback follows the canary run.

### 12.3 Provider acceptance

Each provider packet contains:

- definition and deployed adapter identity;
- entitlement/license decision;
- redacted credential readiness;
- bounded current-host run and row/rejection counts;
- event/available/ingest-time checks;
- watermark and freshness SLA;
- schema and normalization version;
- evidence/search result with citation;
- cost/quota observation; and
- disable/rollback proof.

### 12.4 Product acceptance

A no-route-mock browser test proves:

```text
login as operator
 -> open /management/data-sources
 -> select deployed definition
 -> create source as disabled
 -> validate
 -> run canary
 -> inspect evidence/search receipt
 -> enable
 -> observe desired/actual state
 -> disable
 -> reload browser
 -> confirm disabled state and immutable history
```

The accepted frontend/backend deployment manifest must name the same BFF SHA
returned by `/bff/version`.

## 13. Risk register

| Risk | Control |
|---|---|
| UI enables unintended recurring egress | create-disabled, explicit canary/enable, safe defaults |
| provider secret leaks to browser/log | secret refs only, redaction tests, service resolution |
| proposal says applied without effect | effect/readback receipt required before applied |
| controller overwrites operator changes | revisioned desired state and reconciliation ownership |
| stale process health presented as fresh data | source-specific watermark SLA and observed time |
| duplicate command causes duplicate ingest | BFF and source idempotency keys plus durable receipt |
| paid quota/cost runaway | per-run limits, quota/cost budget and disable gate |
| license leaks into seed/memory | allowed-use propagation and derived-object policy checks |
| semantic search bypasses ACL | pre-retrieval filters shared by all modes |
| external alpha becomes trade intent | no-order invariant and research-only allowed use |
| unsupported provider appears configured | deployed definition check before create |
| phase 2 crosses development boundary | product emits need only; local tooling owns code changes |

## 14. Phase-2 handoff contract

Phase 1 should emit enough information for a future phase-2 system without
implementing that system:

```json
{
  "schema_version": "source_development_need.v1",
  "provider": "example",
  "source_kind": "data_source",
  "source_classes": ["market_daily"],
  "docs_url": "https://provider.example/docs",
  "required_auth_modes": ["api_key"],
  "required_fetch_modes": ["batch"],
  "expected_datasets": ["daily_ohlcv"],
  "license_review_state": "pending",
  "reason": "adapter_not_supported",
  "evidence_refs": [],
  "created_by": "operator",
  "created_at": "RFC3339"
}
```

This artifact is not a development task, approval, connector definition, or
source instance. Phase 2 may transform an operator-approved artifact into a
local development request through development tooling.
