# Pantheon External Data Source Management Current GAP — 2026-08-24

Date: 2026-08-24

Status: current code-first, cross-repository, runtime-aware gap baseline

This document audits the first-stage external-data objective: complete the
source inventory, make every source independently manageable, expose the
management workflow in the Management product, and prove data can move through
evidence, search, research, and governed memory boundaries. OpenClaw-driven
connector discovery or implementation is explicitly deferred to phase 2.

This document is a design input. It does not activate provider egress, add a
vendor credential, place an order, mutate canonical task state, or claim that a
catalog entry is a live connector.

## 1. Executive conclusion

Pantheon has a substantial source-ingestion foundation, but phase 1 is not
complete.

The backend can configure a connector, change its lifecycle, configure a
schedule, run bounded ingestion, persist SourceRecord/EvidenceBundle/
KnowledgeObject objects, notify search, track watermarks, expose health, and
record audit data. A code-owned financial source catalog and a provider-adapter
allowlist also exist.

The product-level management workflow is incomplete:

1. `/management/data-sources` is a read-only health and evidence page. Its
   `controls` column contains side-effect badges and an Evidence link, not
   create, validate, enable, disable, resume, schedule, canary, or retire
   commands.
2. The BFF exposes connector and proposal reads, DLQ/frontier replay, and search
   refresh/materialize. It does not expose governed connector create,
   lifecycle, schedule, validation, canary, or retirement commands.
3. Source-change proposal `apply` records status and a `change_ref`; it does not
   itself mutate DataSourceRegistry, connector configuration, lifecycle, or
   schedule.
4. Proposal types include add, disable, retire, replace, schedule, universe
   policy, and vendor quote, but omit explicit enable, pause, resume, credential
   reference change, and canary activation.
5. Catalog, registry, connector config, schedule, runtime health, and deployed
   adapter identity are separate surfaces without one canonical per-source
   management read model.
6. Several required source families have no real provider implementation or no
   current credentialed/current-host proof.
7. Governed search is deterministic keyword substring retrieval, not the
   specified hybrid/vector/structured-rule search. `time_window` is accepted
   but not applied.
8. External evidence can become a StrategySpecSeed, but completed research does
   not have a production writeback into institutional `research_finding`
   memory. Raw source data correctly does not become memory directly.
9. The standing dev source posture remains external-egress deny plus
   reconcile-only. This is a valid safe posture, but it means process health or
   catalog presence cannot be reported as continuous live data coverage.

Therefore the current maturity is:

```text
source contracts and bounded ingestion       implemented
per-source runtime APIs                       partially implemented
governed Management control plane             missing
complete provider coverage                    missing
structured alpha/rule search                  missing
evidence-to-reviewed-memory closed loop       partial
current hosted end-to-end acceptance          missing
OpenClaw connector development                phase 2, intentionally excluded
```

## 2. Frozen audit baseline

### 2.1 Repository refs

| Repository | Audited ref | SHA |
|---|---|---|
| `ajoe734/pantheon` | `origin/dev` | `40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0` |
| `ajoe734/execute-plans` | `origin/dev` | `5447d2a09b5c83a4f9ee2d405f57c642913e0055` |

Pantheon was audited from a clean task worktree based on `origin/dev`. The
dirty shared checkout under `/home/lupin/pantheon` was not used as source
truth. Frontend paths below refer to the separate `ajoe734/execute-plans`
repository and must not be materialized inside Pantheon.

### 2.2 Hosted dev observation

Observation time: 2026-08-24, approximately 01:06 UTC.

| Surface | Observation |
|---|---|
| FE deployment manifest | accepted, read-only profile, FE `5447d2a0...` |
| Manifest-declared BFF | `bc06779f...` |
| Live BFF `/bff/version` | `40de8fcb...` |
| BFF health | HTTP 200, ready |
| FE build posture | `VITE_BFF_REAL_WRITES=false` |

The live BFF is newer than the BFF identity embedded in the accepted frontend
deployment manifest. This does not by itself make the current page unusable,
but it prevents the manifest from serving as exact-pair acceptance evidence for
new Management command flows.

### 2.3 Classification

Findings use these classes:

- **contract gap** — required request, response, state, or authority is absent;
- **code gap** — a required implementation effect or caller is absent;
- **product gap** — backend capability is not available as a truthful user
  workflow;
- **provider gap** — a source family has no supported adapter or no admitted
  provider;
- **runtime/config gap** — code exists but current runtime posture does not
  exercise it;
- **acceptance gap** — the path may exist but current hosted proof is missing;
- **documentation drift** — current design language does not match code or the
  active repositories.

## 3. Required phase-1 capability

Phase 1 must allow an authorized operator to manage each external data source
independently:

```text
list definitions and instances
  -> inspect license, entitlement, credential state and dependencies
  -> add a supported source as disabled
  -> validate config without fetching arbitrary data
  -> run one bounded read-only canary
  -> inspect SourceRecord/Evidence/Search readback
  -> enable or keep disabled
  -> change schedule/universe policy
  -> observe freshness/watermark/errors/cost/usage
  -> degrade, disable, resume, replace or retire
```

A provider requiring new code is not silently accepted. Phase 1 reports
`adapter_not_supported` and records the required engineering scope. Automatic
OpenClaw development is phase 2.

## 4. Existing implementation to retain

### 4.1 Source-ingest service

The existing source owner already exposes:

- connector configure, list, detail, and lifecycle;
- schedule configure/detail and scheduled-run execution;
- manual/bounded job execution;
- frontier, replay, DLQ, receipts, watermarks, and audit;
- SourceRecord, EvidenceItem, EvidenceBundle, and KnowledgeObject reads;
- health, usage, retirement recommendation, coverage matrix, and alerts;
- a financial data-source catalog and active-universe policy;
- LLM-originated source-change draft proposals; and
- bounded `external_feed`, `static_records`, and
  `provider_owned_adapter` fetch modes.

These are valid building blocks. Phase 1 should compose and harden them rather
than create a second source service.

### 4.2 Code-owned provider adapter allowlist

`services/source_ingestion/provider_adapters.py` currently admits these main
adapters:

- TWSE/TPEx official market data;
- FinMind dataset, broker report, and bulk backfill;
- Yahoo Taiwan broker top and RSS;
- Anue Taiwan RSS;
- MOPS;
- TEJ;
- SEC EDGAR;
- FRED;
- FINRA short sale;
- Stooq daily OHLCV;
- CoinGecko spot market;
- Polygon daily OHLCV;
- Alpha Vantage daily OHLCV;
- IBKR readback; and
- Shioaji readback.

An allowlisted adapter proves supported code exists. It does not prove that the
source is configured, credentialed, scheduled, fresh, or live in the current
environment.

### 4.3 Existing Management read page

The active frontend has:

- route: `/management/data-sources`;
- implementation:
  `execute-plans/src/management/pages/oversight/DataSourceManagement.tsx`;
- read client: `managementConsoleReads.dataSources()`;
- BFF route: `GET /bff/management/data-sources`.

The page displays source/provider identity, health, credential state,
live-ingestion state, read/write capability, consumer personas, evidence refs,
and side-effect status. It supports refresh and evidence navigation.

This is an appropriate read baseline. It is not yet a management control plane.

## 5. Source coverage audit

### 5.1 Financial catalog truth

All current financial catalog entries are onboarding candidates, not live
claims.

| Source/provider | Adapter/code state | Catalog state | Phase-1 gap |
|---|---|---|---|
| FinMind | implemented | candidate | current credential/freshness proof |
| TWSE/TPEx | implemented | candidate | current-host recurring/manual operating proof |
| TDCC | missing | candidate/template disabled | implement adapter and canary |
| TAIFEX | missing | candidate/template disabled | implement adapter and canary |
| MOPS | implemented | candidate | current-host freshness/search proof |
| TEJ | implemented | candidate | entitlement and purchased-table proof |
| Yahoo Taiwan | implemented | candidate | license/robots/freshness proof |
| Anue | implemented | candidate | license/robots/freshness proof |
| SEC EDGAR | implemented | candidate | current-host canary/freshness proof |
| FRED | implemented | candidate | credential/current-host proof |
| FINRA | implemented | candidate | publication-delay/freshness proof |
| CoinGecko | implemented | candidate | current-host canary/freshness proof |
| Polygon | implemented | candidate | credential unavailable/current proof |
| Alpha Vantage | implemented but disabled | candidate/template disabled | key, quota and bounded fallback proof |
| IBKR readback | implemented | candidate | file/readback source identity and freshness |
| Shioaji readback | implemented | candidate | file/readback source identity and freshness |
| Stooq | implemented but disabled | outside main entry list | endpoint/runtime verification |

### 5.2 Required source-family coverage

| Source family | Current state | Gap |
|---|---|---|
| Reference/master/calendar | scattered across official sources | no unified security/calendar identity and coverage contract |
| Market data | multiple adapters | no per-market accepted coverage and freshness matrix |
| Corporate actions | represented in MOPS/design | no complete cross-market PIT acceptance proof |
| Filings/fundamentals | MOPS/SEC paths exist | current-host and restatement/backfill proof incomplete |
| Macro/rates/calendar | FRED path exists | provider/country coverage and release-calendar rules incomplete |
| News | Yahoo/Anue/FinMind metadata | vendor coverage, full-text rights and dedup incomplete |
| Social | generic/static example only | no real provider connector or moderation/bot/deletion policy |
| External Alpha DB | generic/static example only | no real vendor, rule schema, versioned signal query or canary |
| Research corpus | OpenAlex and allowlisted GitHub paths | arXiv/SSRN/PDF admission and corpus coverage incomplete |
| Broker/execution readback | IBKR/Shioaji and other runtime paths | source management does not present one canonical read-only coverage view |
| Telemetry/runtime | separate product owners | source lineage into research evidence not uniformly declared |
| Alternative data | no admitted provider | provider, schema, license and value gate absent |

### 5.3 Alpha terminology collision

The current documents and product surfaces use “Alpha” for distinct concepts:

1. Alpha Vantage, a market-data provider;
2. External Alpha DB, a vendor signal/factor database;
3. Alpha replication, a seed-to-experiment validation loop; and
4. Alpha Factory, a product read surface.

Phase 1 must define separate identifiers and acceptance criteria. A working
Alpha Vantage connector does not close External Alpha DB; a working alpha
replication worker does not prove external alpha discovery.

## 6. Detailed GAP matrix

| ID | Area | Class | Priority | Current truth | Required closure |
|---|---|---|---|---|---|
| SRCM-G01 | canonical source view | contract/product | P0 | catalog, registry, config, schedule, health and adapter identity are separate | one per-source definition/instance/desired/observed read model |
| SRCM-G02 | lifecycle symmetry | contract | P0 | connector supports enabled/disabled/degraded; proposal omits enable/resume | explicit validate/enable/disable/degrade/resume/retire transitions |
| SRCM-G03 | add source | code/product | P0 | source-ingest configure exists, Management cannot use it | governed create-disabled workflow for supported definitions |
| SRCM-G04 | proposal apply | code | P0 | apply changes proposal state and records change_ref only | actual mutation receipt or an honest non-applied state |
| SRCM-G05 | BFF commands | product/code | P0 | only DLQ/frontier replay and search index commands | source create/lifecycle/schedule/canary/retire BFF command facade |
| SRCM-G06 | Management UI | product | P0 | read-only table with evidence link | detail drawer, add wizard, row actions, receipts and confirmation |
| SRCM-G07 | write authority | contract/code | P0 | ordinary connector mutation and proposal actions lack uniform service auth | BFF RBAC plus BFF-to-source service token on every mutation |
| SRCM-G08 | desired/observed state | contract | P0 | lifecycle, schedule and health can disagree without one revision | revisioned desired state and reconciled observed state |
| SRCM-G09 | canary gate | contract/code | P0 | manual job can fetch, but no first-class activation canary result | bounded, read-only, idempotent canary with evidence/search readback |
| SRCM-G10 | provider coverage | provider | P0/P1 | TDCC/TAIFEX/social/alpha DB/alternative missing or example-only | provider-specific implementation and acceptance matrix |
| SRCM-G11 | live truth | runtime/acceptance | P0 | dev defaults deny egress and reconcile only | explicitly labelled manual/scheduled mode plus current run/freshness proof |
| SRCM-G12 | search semantics | code/contract | P0 | keyword substring search; time_window unused | as-of/time filters, structured rule search and hybrid retrieval |
| SRCM-G13 | External Alpha DB | provider/contract | P0 | static example only | vendor-neutral signal contract plus at least one real admitted provider |
| SRCM-G14 | evidence-to-seed | acceptance | P1 | code path exists, external-provider E2E not current | provider SourceRecord through seed/promotion/experiment evidence |
| SRCM-G15 | research memory | code | P1 | research_finding schema exists; production research writer absent | reviewed research result writeback and retrieval proof |
| SRCM-G16 | license propagation | contract | P0 | source evidence carries license; derived memory enforcement incomplete | derived seed/search/memory retains allowed-use and deletion constraints |
| SRCM-G17 | usage/cost | product | P1 | health/usage APIs exist, page does not expose decisions | quota, cost, yield and dependent-consumer view |
| SRCM-G18 | retirement safety | product/contract | P1 | recommendation engine exists | dependency gate, observation window and reversible disable before retire |
| SRCM-G19 | deployment identity | acceptance | P0 | hosted manifest BFF SHA differs from live BFF version | exact FE/BFF pair and write-profile acceptance |
| SRCM-G20 | documentation drift | documentation | P1 | legacy frontend and target/live language remain | current execute-plans paths and explicit target/configured/live terms |

## 7. Root-cause analysis

### 7.1 Definition, instance, and observation are conflated

A code-supported connector definition, an operator-created source instance, a
runtime connector config, and a healthy ingest run are different facts. Current
read surfaces expose pieces of each, so the UI must infer credential and live
states from status strings.

Required separation:

```text
ConnectorDefinition  code-owned capability and adapter version
DataSourceEntry      operator-owned source instance and policy
ConnectorConfig      controller-owned executable projection
SourceDesiredState   requested lifecycle/schedule/universe revision
SourceObservedState  health/watermark/run/error/current deployment
```

The final Management DTO composes these objects; it does not create a second
authority.

### 7.2 Existing mutation routes are not a product workflow

Direct source-ingest routes are service-level primitives. The browser must not
call them directly. The BFF needs command admission, RBAC, idempotency,
confirmation, service authentication, error normalization, and command
receipts. Those controls currently exist for selected source/search operations,
but not source lifecycle management.

### 7.3 “Applied” is stronger than the implemented effect

`SourceChangeProposalStore.apply()` transitions an approved proposal to
`applied` and optionally appends `applied_change_refs`. No registry or connector
mutation is performed by that call. A proposal must not become `applied` until
the intended owner has produced a durable effect receipt and readback confirms
it.

### 7.4 Safe dev posture is being mistaken for missing capability or live proof

`PANTHEON_EXTERNAL_EGRESS=deny`, an empty host allowlist, and a single
non-restarting `reconcile_only` tick are intentional dev defaults. Phase 1
must retain safe bounded activation while making the operating mode visible.
The explicit provider-pull profile must require one bounded
`reconcile_and_pull` run plus exact connector and host allowlists. It must not
solve the management gap by enabling unrestricted recurring provider pulls.

## 8. Search, Alpha DB, and knowledge GAP

### 8.1 Governed search

Current search correctly performs license/access/persona/workspace/environment
checks before ranking and requires citations by default. It also rejects
future `available_time` evidence.

Missing behavior:

- `time_window` execution;
- explicit `available_time_lte` for historical/as-of queries;
- role, sensitivity, and capital-pool scope;
- full-text and semantic/vector retrieval;
- fielded structured filters;
- stable query/result snapshot identity; and
- rule/factor query execution.

### 8.2 Structured alpha/rule search

External alpha cannot be treated as a text document only. Phase 1 needs a
versioned signal contract and query grammar covering:

- vendor/signal/version;
- universe and asset identifiers;
- factor expression or rule AST;
- field types, units and currency;
- event/as-of/available/ingest time;
- corporate-action and survivorship policy;
- license, allowed use and entitlement;
- query fingerprint and immutable result snapshot; and
- provider cost/quota receipt.

Structured results may attach EvidenceBundle citations, but the query engine
must not infer tradability or route orders.

## 9. Evidence, seed, memory, and inspiration GAP

The correct boundary is:

```text
raw provider response
  -> SourceRecord / EvidenceItem / EvidenceBundle / KnowledgeObject
  -> governed search and cross-source validation
  -> reviewed research finding or StrategySpecSeed
  -> experiment/promotion outcome
  -> PersonaMemory / InstitutionalMemory
```

Raw external content must not be written directly to long-term memory. Current
memory writeback handles runtime telemetry outcomes, published postmortems, and
approved evolution decisions. The design note also declares research
`done -> research_finding`, but the production research service does not make
that write.

The current Inspiration Graph is primarily artifact lineage. It must not be
used as proof that memory caused a new idea unless an influence edge carries a
real source type, retrieval/query ref, evidence refs, derivation method, and
non-constant weight.

## 10. Management product GAP

### 10.1 Current page

`/management/data-sources` is a useful operator overview. It must be retained
and expanded rather than replaced by a second page with overlapping truth.

Current controls:

- refresh;
- focus by persona/provider;
- evidence navigation;
- health/credential/live/read-only/side-effect badges.

Missing controls:

- add supported source;
- source detail with desired versus observed state;
- validate config;
- attach/change secret reference without revealing a secret;
- run bounded canary;
- enable/disable/degrade/resume;
- edit schedule and active-universe policy;
- inspect watermarks/runs/DLQ/usage/cost;
- replace/retire with dependency checks; and
- view immutable action receipts and current deployment identity.

### 10.2 Product truth requirements

The page must distinguish:

| Label | Meaning |
|---|---|
| `supported` | adapter exists in the deployed build |
| `configured` | instance/config exists |
| `credential_ready` | secret reference resolves with required scopes |
| `validated` | config and policy validation passed |
| `canary_passed` | bounded current-deployment read and downstream readback passed |
| `enabled` | desired lifecycle permits scheduled/manual execution |
| `fresh` | latest observed watermark is within source-specific SLA |
| `degraded` | source is usable only with explicit degraded reason |
| `disabled` | execution is denied; historical evidence remains readable |
| `retired` | terminal for new ingest; history/lineage retained per policy |

No UI adapter may infer these states from provider-specific free text.

## 11. Phase boundary

### Phase 1 — external data completion and operator management

Included:

- source definition/instance/desired/observed contracts;
- per-source create-disabled, validate, canary, enable, disable, resume,
  schedule, replace, and retire workflows;
- BFF command facade and Management UI;
- required provider implementations and current-host evidence;
- governed search, structured alpha query, evidence/seed/readback;
- reviewed research-finding memory writeback; and
- exact hosted acceptance.

### Phase 2 — OpenClaw-assisted source evolution

Deferred:

- autonomous provider discovery;
- API/document analysis;
- LLM-created source-change proposal UI/actions;
- automatic connector development requests;
- local development-tooling handoff and implementation tracking; and
- AI recommendations for source replacement, cost optimization, or retirement.

Phase 2 may consume phase-1 definitions and receipts. It may not weaken phase-1
operator gates or let product runtime write repository files.

## 12. Phase-1 definition of done

Phase 1 is complete only when all conditions hold:

1. Management lists every supported definition and configured source instance
   with non-inferred desired and observed state.
2. An authorized operator can add a supported source as disabled, validate it,
   run a bounded canary, enable it, disable it, and resume it through BFF-backed
   commands.
3. Every mutation is authenticated, idempotent, audited, revision-checked, and
   returns an effect/readback receipt.
4. A disabled source cannot run manually or on schedule; a retired source cannot
   be re-enabled without a new source identity.
5. Provider coverage has an honest status for all required source families; no
   example/catalog-only entry is labelled live.
6. At least one real External Alpha DB provider passes PIT/license/rule-query/
   evidence readback, or the capability remains explicitly unavailable rather
   than “complete.”
7. Search applies as-of and time-window filters and supports the approved
   structured alpha query contract.
8. A real external source can be traced through SourceRecord, EvidenceBundle,
   search, StrategySpecSeed/research result, experiment outcome, and reviewed
   memory writeback without direct order side effects.
9. Hosted no-route-mock browser acceptance proves add-disabled, canary,
   enable/disable, reload/readback, and negative authorization paths against an
   exact FE/BFF pair.
10. OpenClaw remains outside source mutation and repository-development
    authority until phase 2.

## 13. Files inspected

Pantheon:

- `services/source_ingestion/main.py`
- `services/source_ingestion/configured.py`
- `services/source_ingestion/provider_adapters.py`
- `services/source_ingestion/financial_source_catalog.py`
- `services/source_ingestion/connectors/`
- `services/source_ingestion/registry/`
- `services/source_ingestion/policy_registry.py`
- `services/search/filters.py`
- `services/search/gateway.py`
- `services/search/retriever.py`
- `services/memory/learn_feedback_writeback.py`
- `services/persona/learning_feedback_bridge.py`
- `services/control-plane/bff/console_gap/datasources.py`
- `services/control-plane/bff/source_search_ops_client.py`
- `services/control-plane/bff/main.py`
- `docker-compose.yml`
- `docs/03/SD-03_source_knowledge_evidence.md`
- `docs/04/pantheon_sa/SA-16_data_search_external_source_gap_analysis.md`
- `docs/04/pantheon_data_strategy_source_design_2026-06-09/`

Execute Plans:

- `execute-plans/src/App.tsx`
- `execute-plans/src/management/pages/oversight/DataSourceManagement.tsx`
- `execute-plans/src/lib/v5/management/systemDataSources.ts`
- `execute-plans/src/lib/bff-v1/managementConsoleReads.ts`
- `execute-plans/src/management/navigation/managementRouteManifest.ts`
