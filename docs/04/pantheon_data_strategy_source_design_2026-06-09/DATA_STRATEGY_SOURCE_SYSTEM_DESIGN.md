# Pantheon Data Source / Strategy Seed Source System Design

Generated: 2026-06-09

Status: design-development spec

Owner: Pantheon research / source / persona planes

Related docs:

- `docs/03/SD-02_persona_governance.md`
- `docs/03/SD-03_source_knowledge_evidence.md`
- `docs/03/SD-04_research_orchestrator.md`
- `docs/contracts/data_source_registry_entry.schema.json`
- `docs/contracts/strategy_seed_source_registry_entry.schema.json`
- `docs/contracts/source_change_proposal.schema.json`
- `docs/contracts/persona_strategy_match.schema.json`
- `docs/contracts/strategy_spec_seed.schema.json`
- `Pantheon_資料表_Schema_設計版.md`

## 1. Executive Decision

本文件把前面討論正式拆成三個不同模組：

1. `Data Source Management`
   - 管市場資料、財報、新聞、籌碼、總經、公告、行情等資料供應。
   - 產物是 raw / normalized / features / health / gap report。
   - 例子：TWSE、TPEx、MOPS、FinMind、TEJ、Yahoo、TDCC、TAIFEX、SEC EDGAR、FINRA、FRED、Stooq、Polygon。

2. `Strategy Seed Source Management`
   - 管策略想法、alpha hypothesis、論文、repo、內部研究筆記、外部 alpha database。
   - 產物是 `StrategySpecSeed`、`AlphaTemplate`、evidence bundle、source lineage。
   - 例子：OpenAlex / arXiv / SSRN papers、QuantConnect / LEAN examples、Qlib / FinRL / vectorbt repos、內部 memo、歷史 experiment telemetry、Persona 提案。

3. `Persona Strategy Discovery`
   - 讓 Persona 依照自己的初始設定，查找相似策略種子或策略規格。
   - 產物是 explainable strategy match、research ticket、StrategySpec candidate。
   - Persona 不直接拿 match 去 live trading；必須經研究、回測、promotion、deployment gate。

這個拆分是必要的，因為「資料來源」與「策略來源」是不同語意：

```text
Data Source:
  給策略研究與模型用的資料。

Strategy Seed Source:
  給系統產生策略假設與 StrategySpecSeed 的知識來源。
```

同一個供應商可能同時存在於兩邊，但角色必須分清楚。例如 TEJ 可以是歷史行情/財報/籌碼資料來源；只有當 TEJ 提供的是策略/因子/研究想法時，才同時算 strategy seed source。FinMind 目前主要應歸類為 data source，不應直接歸類為 strategy source。

## 2. Current State Audit

### 2.1 Already Implemented

Repo 目前已有以下骨架：

| Capability | Current implementation | Status |
|---|---|---|
| Generic source connector domain | `services/source_ingestion/connectors/base.py` defines `SourceConnector`, `SourceType`, auth, license, lifecycle, ingest run models | partial |
| Source connector configure/list/get | `POST /api/source-ingest/connectors`, `GET /api/source-ingest/connectors`, `GET /api/source-ingest/connectors/{id}` | partial |
| Connector lifecycle control | `PUT /api/source-ingest/connectors/{id}/lifecycle` supports enabled / disabled / degraded with audit | partial |
| Connector schedule | `PUT /api/source-ingest/connectors/{id}/schedule`, `POST /api/source-ingest/run-scheduled` | partial |
| Source registry projection | `GET /api/source-ingest/registry`, `GET /api/source-ingest/policy-registry` | partial |
| Source evidence persistence | JSONL default and optional Postgres `source_ingest.source_evidence` | partial |
| Strategy seed builder | `services/source_ingestion/strategy_seed_builder.py` | library only |
| Strategy seed to StrategySpec conversion | `services/research/strategy_spec/conversion.py` | library only |
| Registry facade accepts source seed lineage | `services/registry/service.py` supports `source_seed_id` / lineage requirement | partial |
| Persona registry has initial settings | `mandate`, `strategy_family`, lifecycle, workspace/policy refs | partial |
| BFF strategy spec filter by persona id | `read_store.list_strategy_specs(persona_id=...)` | existing links only |
| BFF read-only source connector view | `GET /api/v1/research/source-connectors` reads source-ingest registry | read only |

### 2.2 Not Implemented End To End

The following are not complete:

| Gap | Why it matters |
|---|---|
| Dedicated data source registry | Current `source_ingestion` uses a broad `source` concept. It does not clearly separate data supply from strategy knowledge supply. |
| Dedicated strategy seed source registry | Paper/repo/alpha DB examples exist, but not as a managed strategy source product surface. |
| Strategy seed persistence | `StrategySpecSeedBuilder` exists, but there is no production `strategy_spec_seed_store` or API. |
| Ingest to seed materialization | Source ingest does not automatically run `EvidenceBundle -> StrategySpecSeed`. |
| Persona strategy discovery | No service reads Persona initial settings and searches similar strategy seeds/specs. |
| Similarity scoring/index | No deterministic or embedding-based strategy similarity index. |
| LLM source-change proposals | No governed proposal workflow for adding, disabling, replacing, or retiring sources. |
| Usage-based retirement | No usage telemetry for source cost/yield/usage, so low-value sources cannot be retired safely. |
| Production financial source catalog | FinMind, TEJ, Yahoo, TWSE/TPEx, MOPS, SEC, FRED etc. are not yet formal production connectors in this design surface. |

## 3. Architecture Overview

### 3.1 Target Plane Split

```text
                        +----------------------+
                        | Persona Registry      |
                        | mandate / family /    |
                        | risk / markets        |
                        +-----------+----------+
                                    |
                                    v
                        +----------------------+
                        | Persona Strategy      |
                        | Discovery             |
                        +-----------+----------+
                                    |
              +---------------------+---------------------+
              |                                           |
              v                                           v
 +--------------------------+                +--------------------------+
 | Strategy Seed Registry   |                | StrategySpec Registry     |
 | StrategySpecSeed         |                | approved/candidate specs  |
 | AlphaTemplate            |                | lineage / lifecycle       |
 +-------------+------------+                +-------------+------------+
               ^                                           ^
               |                                           |
 +-------------+------------+                              |
 | Strategy Seed Source     |                              |
 | Management               |                              |
 | papers / repos / notes   |                              |
 +-------------+------------+                              |
               ^                                           |
               | evidence                                  |
 +-------------+------------+                              |
 | Source Evidence Store    |------------------------------+
 | SourceRecord / Evidence  |
 | Bundle / KnowledgeObject |
 +-------------+------------+
               ^
               |
 +-------------+------------+
 | Data Source Management   |
 | market / filing / news / |
 | macro / financial data   |
 +--------------------------+
```

### 3.2 Responsibility Boundaries

| Module | Owns | Does not own |
|---|---|---|
| Data Source Management | Data connectors, schedules, watermarks, raw/normalized/features storage, data health | Strategy idea quality, Persona matching |
| Strategy Seed Source Management | Paper/repo/note/alpha source admission, evidence bundles, StrategySpecSeed materialization | Market data storage, trading execution |
| Persona Strategy Discovery | Persona profile extraction, strategy similarity search, explainable recommendations | Direct strategy deployment, direct broker action |
| Research Orchestrator | Experiment tasks/runs, backend selection, metric bundles | Source authority, live deployment |
| Registry | StrategySpec lifecycle, lineage, artifact records | Raw data ingestion |
| Promotion / Deployment | Approval and runtime binding | Discovery or source crawling |

## 4. Data Source Management

### 4.1 Purpose

Data Source Management is responsible for obtaining and maintaining research data. It must answer:

- What data do we have?
- From which provider?
- Under what license and entitlement?
- How fresh is it?
- Which symbols/universes are updated?
- Which datasets are stale, missing, expensive, or unused?
- Which strategy seeds/specs require this data?

### 4.2 Source Classes

| Class | Examples | Primary use | Update profile |
|---|---|---|---|
| Market daily data | TWSE, TPEx, FinMind, TEJ, Stooq, Polygon | price/volume, OHLCV, returns | daily |
| Intraday/quote data | Shioaji quote, broker APIs, Polygon | monitoring, execution research | near-real-time or sampled |
| Filings/events | MOPS, SEC EDGAR | financial statements, material events | event/daily |
| Financial fundamentals | MOPS, TEJ, FinMind | revenue, income statement, balance sheet | daily/monthly/quarterly |
| Taiwan chip data | TWSE, TPEx, TDCC, TAIFEX, Yahoo, FinMind, TEJ | institutions, margin, lending, futures, broker top N | daily/weekly |
| News | MOPS, Yahoo RSS, Anue RSS, other RSS | event detection and research context | 10-30 minutes |
| Macro | FRED, central bank/government sources | rates, inflation, macro regimes | daily/weekly/monthly |
| Short interest | FINRA, exchange data | US short activity | daily/half-month |
| Vendor backfill | TEJ, exchange paid history | historical gap fill | one-time or limited term |

### 4.3 Initial Data Source Catalog

This catalog is a target onboarding plan, not a claim that each connector is already live.

| Dataset | Preferred source | Backup/backfill | Storage tier | Notes |
|---|---|---|---|---|
| Taiwan daily price | TWSE/TPEx OpenAPI, FinMind | TEJ | normalized/features | Official source first; FinMind can simplify ingestion. |
| Taiwan monthly revenue | MOPS, FinMind | TEJ | normalized/features | Daily scan, monthly event facts. |
| Taiwan financial statements | MOPS, FinMind | TEJ | normalized/features | Quarterly facts with PIT availability. |
| Taiwan material information | MOPS | TEJ/news RSS | raw/normalized | Event stream. |
| Institutions | TWSE/TPEx, FinMind | TEJ | normalized/features | Daily. |
| Margin/short/margin purchase | TWSE/TPEx, FinMind | TEJ | normalized/features | Daily. |
| Securities lending | TWSE/TPEx, FinMind | TEJ | normalized/features | Daily. |
| Day trading | TWSE/TPEx, FinMind | TEJ | normalized/features | Daily. |
| TDCC shareholding distribution | TDCC, FinMind if available | TEJ | normalized/features | Weekly. |
| Futures chip data | TAIFEX OpenAPI | none | normalized/features | Daily. |
| Broker top 15/20 | Yahoo broker trading, FinMind if available | TEJ AMTOP1 / ABSR20 | normalized/features | Active/candidate universe only. |
| Taiwan news | MOPS, Yahoo RSS, Anue RSS | other RSS | raw/normalized | 10-30 min metadata, full text only if license allows. |
| US daily price | Stooq, Polygon, Alpha Vantage | IBKR | normalized/features | Start with daily; paid realtime later. |
| US filings | SEC EDGAR | none | raw/normalized | Event/daily. |
| US short interest | FINRA | none | normalized/features | Daily and half-month depending dataset. |
| Macro | FRED | none | normalized/features | Mixed frequencies. |

### 4.4 Active Universe Policy

The system must not run full-market full-depth updates for every dataset every day.

Use three universe tiers:

| Tier | Definition | Data policy |
|---|---|---|
| `core_universe` | holdings, active strategies, active research targets | Full data: price, financials, chip, broker top N, news, events |
| `candidate_universe` | likely research/trading candidates | Price, chip summary, Yahoo/FinMind broker top N, news metadata |
| `archive_universe` | removed from research/trading | Daily price and material events only; stop broker/news detail |

Universe transition events must be recorded:

```yaml
UniverseTransition:
  symbol: string
  market: string
  from_tier: core_universe | candidate_universe | archive_universe
  to_tier: core_universe | candidate_universe | archive_universe
  reason: string
  triggered_by: actor_ref
  effective_at: datetime
```

### 4.5 Storage Design

Use three storage layers:

```text
raw/
  source/dataset/date/...

normalized/
  tw_price_daily
  tw_financial_statement
  tw_monthly_revenue
  tw_institutional
  tw_margin_short
  tw_lending
  tw_broker_top
  tw_news_event
  us_price_daily
  sec_filing_event
  macro_fred_observation

features/
  broker_top_concentration
  broker_net_buy_streak
  institutional_net_buy_streak
  revenue_surprise
  filing_event_flags
  macro_regime_features
```

Broker top N should be stored as a compact normalized table:

```yaml
tw_broker_top:
  date: date
  symbol: string
  market: string
  source: string
  side: buy | sell | net
  rank: integer
  broker: string
  buy_qty: number
  sell_qty: number
  net_qty: number
  raw_ref: string
  ingest_run_id: string
```

Do not store full broker-by-stock history unless a paid historical backfill explicitly requires it. The default product requirement is top 15 or top 20 for active/candidate universe.

### 4.6 Data Source Lifecycle

Data sources should not be hard-deleted by default.

```text
draft
→ proposed
→ approved
→ enabled
→ degraded
→ disabled
→ retired
```

Rules:

- `enabled`: scheduler may ingest.
- `degraded`: scheduler may ingest if policy allows; health surface must show warning.
- `disabled`: scheduler must not ingest.
- `retired`: hidden by default but retained for lineage/audit.
- `delete`: only allowed for never-used draft/proposed configs with no evidence, no raw data, no lineage.

## 5. Strategy Seed Source Management

### 5.1 Purpose

Strategy Seed Source Management is responsible for producing research-only strategy seeds. It must answer:

- What strategy ideas do we know?
- Where did each idea come from?
- Is the idea legally usable?
- What data does it require?
- Which asset classes, markets, horizons, and backends does it fit?
- Can it be converted into a StrategySpec candidate?

### 5.2 Strategy Source Classes

| Class | Examples | Output |
|---|---|---|
| Papers | OpenAlex, arXiv, SSRN, DOI references | evidence bundle, StrategySpecSeed |
| Code repos | QuantConnect/LEAN examples, Qlib, FinRL, vectorbt notebooks | code refs, feature hints, backend hints |
| Internal notes | analyst notes, meeting notes, research memo | StrategySpecSeed |
| Experiment telemetry | historical runs, failed/successful metrics | seed refinement, alpha template |
| Persona proposal | Persona-generated hypotheses | draft seed proposal |
| External alpha DB | paid vendor signal/strategy database | restricted seed/evidence |

### 5.3 Strategy Seed Source Catalog

Initial target catalog:

| Source | Type | Role | Admission |
|---|---|---|---|
| OpenAlex | paper | research paper discovery | public/open metadata, bounded external feed |
| arXiv | paper | preprint strategy/factor research | public feed, citation/license checks |
| SSRN | paper | finance research discovery | metadata first; full text only if allowed |
| GitHub allowlist | repo | code examples and strategy implementations | allowlisted owner/repo/path/ref only |
| QuantConnect/LEAN examples | repo | implementation patterns and strategy families | research-only references |
| Qlib examples | repo | ML alpha pipeline patterns | research-only references |
| FinRL examples | repo | RL strategy patterns | research-only references |
| vectorbt notebooks | repo | vectorized backtest patterns | research-only references |
| Internal research notes | internal_note | proprietary hypotheses | internal access scope |
| Experiment telemetry | telemetry | reuse and refine prior research | internal access scope |
| Paid alpha vendor | alpha_db | external signal/idea corpus | restricted, entitlement required |

### 5.4 StrategySpecSeed Store

The current builder should be backed by a persistent store.

```yaml
StrategySpecSeed:
  seed_id: string
  source_ids: string[]
  evidence_bundle_id: string
  hypothesis: string
  strategy_family: string | null
  asset_class: string
  markets: string[]
  universe_hint: string[]
  holding_period: string
  required_data_json: object
  backend_hint: string | null
  feature_hints_json: object
  label_hints_json: object
  risk_notes: string[]
  code_refs_json: object
  license_scope: string
  allowed_use: string[]
  confidence: number
  status: draft | review | accepted | rejected | promoted | retired
  created_at: datetime
  updated_at: datetime
```

Seed store requirements:

- JSONL local implementation for dev.
- Postgres implementation for production.
- Index by family, market, asset class, holding period, required data, backend hint, status.
- Store lineage from source record and evidence item to seed.
- Do not allow direct execution route in seed metadata.
- Keep `research_only=true` until promoted through registry workflow.

### 5.5 Seed Materialization Flow

```text
Strategy Source Connector
→ IngestRun
→ SourceRecord
→ EvidenceItem
→ EvidenceBundle
→ StrategySpecSeedBuilder
→ StrategySpecSeed Store
→ Review Queue
→ StrategySpec Conversion
→ Registry StrategySpec candidate
```

Materialization should be idempotent:

```yaml
SeedMaterializationRequest:
  evidence_bundle_id: string
  source_ids: string[]
  mode: create_if_absent | refresh | force_new_version
  requested_by: actor_ref
  idempotency_key: string
```

## 6. Persona Strategy Discovery

### 6.1 Purpose

Persona Strategy Discovery allows a Persona to find candidate strategies based on its initial settings. It is not deployment automation.

Inputs:

- `persona.mandate`
- `persona.strategy_family`
- workspace / allowed markets
- risk profile
- holding period preferences
- allowed research backends
- allowed source scopes
- current capital/deployment authority
- active universe preferences

Outputs:

- ranked strategy seed matches
- ranked StrategySpec matches
- explanation of match
- missing data requirements
- suggested next action

### 6.2 Persona Profile Extraction

```yaml
PersonaStrategyProfile:
  persona_id: string
  mandate_terms: string[]
  strategy_families: string[]
  preferred_markets: string[]
  asset_classes: string[]
  holding_periods: string[]
  risk_constraints: object
  backend_preferences: string[]
  allowed_source_scopes: string[]
  data_availability_scope: string[]
  lifecycle_state: string
```

Extraction must be deterministic first. LLM-assisted enrichment may propose additional tags, but the final profile must be explainable and reviewable.

### 6.3 Similarity Scoring

Start with deterministic scoring before embeddings.

```yaml
StrategyMatchScore:
  strategy_family_match: 0-20
  market_asset_match: 0-15
  holding_period_match: 0-10
  required_data_available: 0-15
  evidence_quality: 0-15
  backend_compatibility: 0-10
  risk_profile_match: 0-10
  novelty_or_diversification: 0-5
  total: 0-100
```

Hard blockers:

- license does not allow research use
- required data unavailable and no backfill path
- source is disabled/retired
- seed status is rejected
- Persona lifecycle does not allow research
- route policy blocks required backend/source scope
- strategy requires broker/live/execution access during discovery

### 6.4 Strategy Match Result

```yaml
PersonaStrategyMatch:
  match_id: string
  persona_id: string
  matched_object_type: strategy_spec_seed | strategy_spec | alpha_template
  matched_object_id: string
  score: number
  rank: integer
  matched_fields:
    - field: string
      persona_value: string
      strategy_value: string
      contribution: number
  missing_data:
    - dataset: string
      severity: info | warning | blocker
      remediation: string
  evidence_refs: string[]
  source_refs: string[]
  recommended_action:
    type: create_research_ticket | promote_seed_candidate | run_rapid_eval | ignore | request_data_backfill
    reason: string
```

### 6.5 Persona Discovery API

Target service API:

```text
GET /api/personas/{persona_id}/strategy-matches
POST /api/personas/{persona_id}/strategy-discovery-sessions
GET /api/personas/{persona_id}/strategy-discovery-sessions/{session_id}
POST /api/personas/{persona_id}/strategy-matches/{match_id}/actions/create-research-ticket
POST /api/personas/{persona_id}/strategy-matches/{match_id}/actions/promote-seed-candidate
```

BFF API:

```text
GET /api/v1/personas/{persona_id}/strategy-matches
POST /api/v1/personas/{persona_id}/strategy-discovery
POST /api/v1/personas/{persona_id}/strategy-matches/{match_id}/actions
```

BFF must be read/composition-oriented and route writes through command/admission surfaces.

## 7. LLM Source Proposal Governance

### 7.1 Purpose

LLM may suggest adding or retiring data/strategy sources, but must not directly mutate source registry state.

LLM can produce:

- `add_data_source`
- `add_strategy_seed_source`
- `disable_source`
- `retire_source`
- `replace_source`
- `change_schedule`
- `change_universe_policy`
- `request_vendor_quote`

### 7.2 Proposal Model

```yaml
SourceChangeProposal:
  proposal_id: string
  proposal_type: add_data_source | add_strategy_seed_source | disable_source | retire_source | replace_source | change_schedule | change_universe_policy | request_vendor_quote
  target_source_id: string | null
  proposed_source:
    source_id: string
    source_kind: data_source | strategy_seed_source
    provider: string
    source_class: string
    homepage_url: string | null
    docs_url: string | null
    license_scope: string
    entitlement_required: boolean
    allowed_use: string[]
    expected_datasets: string[]
    update_frequency: string
    cost_notes: string | null
  rationale: string
  expected_value:
    coverage_gain: string
    cost_change: string
    reliability_change: string
    strategy_impact: string
  risks:
    - risk_type: license | cost | quality | stability | privacy | operational
      severity: low | medium | high
      note: string
  evidence_refs: string[]
  proposed_by: actor_ref
  status: draft | submitted | approved | rejected | applied | retired
  created_at: datetime
```

### 7.3 Approval Rules

| Proposal | Required approval |
|---|---|
| Add public metadata source | source operator |
| Add paid source | source operator + finance/vendor owner |
| Add source with credentials | source operator + security |
| Add strategy seed source | source operator + research owner |
| Disable low-use source | source operator |
| Retire source with lineage | source operator + research owner |
| Delete source | only if no lineage/evidence/data and explicit admin approval |

### 7.4 Low Usage Retirement Flow

```text
Usage telemetry detects low value
→ LLM drafts retire_source proposal
→ system attaches evidence: usage, failures, cost, replacement
→ operator review
→ disable for observation window
→ no objections / no dependent strategy blockers
→ retire
```

Default observation window: 30 days.

Hard delete is not part of the default path.

## 8. Usage, Yield, And Health Telemetry

### 8.1 Data Source Health

```yaml
SourceHealth:
  source_id: string
  source_kind: data_source | strategy_seed_source
  status: ok | stale | degraded | failed | disabled | retired
  last_success_at: datetime | null
  last_failure_at: datetime | null
  latest_watermark: string | null
  row_count_last_run: integer
  rejected_count_last_run: integer
  schema_hash: string | null
  staleness_seconds: integer | null
  error_rate_7d: number
  cost_estimate_30d: number | null
```

### 8.2 Usage Metrics

```yaml
SourceUsageDaily:
  date: date
  source_id: string
  source_kind: data_source | strategy_seed_source
  ingest_run_count: integer
  query_count: integer
  search_hit_count: integer
  persona_match_count: integer
  strategy_seed_yield_count: integer
  strategy_promotion_count: integer
  experiment_dependency_count: integer
  active_strategy_dependency_count: integer
  cost_estimate: number | null
```

Usage-based recommendations:

| Condition | Recommendation |
|---|---|
| high failure, low usage, no dependencies | propose disable |
| high cost, low yield, has replacement | propose replace |
| high strategy seed yield | keep and consider schedule increase |
| stale but critical dependency | alert and backfill |
| new source, no usage yet | keep in probation window |

## 9. Security And License Rules

Rules:

1. Never store inline API keys in docs, connector config, source records, or seed metadata.
2. Use `secret_ref_id` only.
3. LLM/OpenClaw must not hold vendor tokens directly.
4. Sources with paid or restricted license require entitlement tags.
5. News, social, alpha DB, macro, market data must preserve point-in-time fields where applicable:
   - `event_time`
   - `available_time`
   - `ingest_time`
6. Strategy seeds are research-only until promoted.
7. Source discovery cannot call broker, runtime, order router, live, or Lean direct execution paths.
8. BFF must not become canonical source owner.

## 10. Required Schema Additions

### 10.1 Data Source Registry

Contract stub: `docs/contracts/data_source_registry_entry.schema.json`

```yaml
registry.data_sources:
  data_source_id: text primary key
  provider: text
  source_class: text
  datasets_json: jsonb
  license_scope: text
  entitlement_tags_json: jsonb
  allowed_use_json: jsonb
  update_frequency: text
  universe_policy_ref: text
  lifecycle_state: text
  connector_id: text
  created_at: timestamptz
  updated_at: timestamptz
```

### 10.2 Strategy Seed Source Registry

Contract stub: `docs/contracts/strategy_seed_source_registry_entry.schema.json`

```yaml
registry.strategy_seed_sources:
  strategy_seed_source_id: text primary key
  provider: text
  source_class: text
  source_scope: text
  license_scope: text
  entitlement_tags_json: jsonb
  allowed_use_json: jsonb
  crawler_policy_ref: text
  lifecycle_state: text
  connector_id: text
  created_at: timestamptz
  updated_at: timestamptz
```

### 10.3 Strategy Seed Store

```yaml
source.strategy_spec_seeds:
  seed_id: text primary key
  evidence_bundle_id: text
  source_ids_json: jsonb
  hypothesis: text
  strategy_family: text
  asset_class: text
  markets_json: jsonb
  holding_period: text
  required_data_json: jsonb
  backend_hint: text
  feature_hints_json: jsonb
  label_hints_json: jsonb
  risk_notes_json: jsonb
  code_refs_json: jsonb
  license_scope: text
  allowed_use_json: jsonb
  confidence: numeric
  status: text
  created_at: timestamptz
  updated_at: timestamptz
```

### 10.4 Source Change Proposal

Contract stub: `docs/contracts/source_change_proposal.schema.json`

```yaml
governance.source_change_proposals:
  proposal_id: text primary key
  proposal_type: text
  source_kind: text
  target_source_id: text
  payload_json: jsonb
  rationale: text
  evidence_refs_json: jsonb
  proposed_by_json: jsonb
  status: text
  created_at: timestamptz
  updated_at: timestamptz
```

### 10.5 Persona Strategy Match

Contract stub: `docs/contracts/persona_strategy_match.schema.json`

```yaml
research.persona_strategy_matches:
  match_id: text primary key
  persona_id: text
  matched_object_type: text
  matched_object_id: text
  score: numeric
  score_breakdown_json: jsonb
  matched_fields_json: jsonb
  missing_data_json: jsonb
  recommended_action_json: jsonb
  evidence_refs_json: jsonb
  status: text
  created_at: timestamptz
```

## 11. API Surface Plan

### 11.1 Data Source APIs

```text
GET  /api/data-sources
POST /api/data-sources
GET  /api/data-sources/{source_id}
PUT  /api/data-sources/{source_id}/lifecycle
PUT  /api/data-sources/{source_id}/schedule
GET  /api/data-sources/{source_id}/health
GET  /api/data-sources/{source_id}/usage
POST /api/data-sources/{source_id}/runs
GET  /api/data-sources/{source_id}/gap-report
```

Implementation can initially wrap existing `source-ingest` connector APIs while using data-source-specific naming at BFF/product level.

### 11.2 Strategy Seed Source APIs

```text
GET  /api/strategy-seed-sources
POST /api/strategy-seed-sources
GET  /api/strategy-seed-sources/{source_id}
PUT  /api/strategy-seed-sources/{source_id}/lifecycle
POST /api/strategy-seed-sources/{source_id}/runs
POST /api/strategy-seeds/materialize
GET  /api/strategy-seeds
GET  /api/strategy-seeds/{seed_id}
POST /api/strategy-seeds/{seed_id}/actions/promote-to-strategy-spec
```

### 11.3 Proposal APIs

```text
GET  /api/source-change-proposals
POST /api/source-change-proposals
GET  /api/source-change-proposals/{proposal_id}
POST /api/source-change-proposals/{proposal_id}/actions/submit
POST /api/source-change-proposals/{proposal_id}/actions/approve
POST /api/source-change-proposals/{proposal_id}/actions/reject
POST /api/source-change-proposals/{proposal_id}/actions/apply
```

### 11.4 BFF Read Surfaces

```text
GET /api/v1/research/data-sources
GET /api/v1/research/strategy-seed-sources
GET /api/v1/research/strategy-seeds
GET /api/v1/research/source-change-proposals
GET /api/v1/personas/{persona_id}/strategy-matches
```

## 12. Development Plan

### PR 1: System Design And Contracts

Scope:

- Add this design document.
- Add JSON schema stubs for:
  - data source registry entry
  - strategy seed source registry entry
  - source change proposal
  - persona strategy match

Validation:

- markdown links/path sanity
- schema JSON parses

### PR 2: Registry Split Layer

Scope:

- Add `DataSourceRegistry` abstraction.
- Add `StrategySeedSourceRegistry` abstraction.
- Back both with JSONL for dev.
- Map existing `SourceConnector` into one of the two product-level registries.
- Keep `source_ingestion` generic internals intact.

Acceptance:

- Can add/list/get lifecycle for data source.
- Can add/list/get lifecycle for strategy seed source.
- Existing source-ingest connector tests continue passing.

### PR 3: Financial Data Source Catalog

Scope:

- Add connector definitions/config templates for:
  - FinMind
  - TWSE/TPEx
  - MOPS
  - Yahoo RSS / broker top N
  - SEC EDGAR
  - FRED
- Add source health projection.
- Add active/candidate/archive universe-aware scheduling.

Acceptance:

- Connectors can be configured without inline secrets.
- Scheduler can skip archive universe expensive datasets.
- Health surface shows `last_success_at`, `watermark`, row count, staleness.

### PR 4: Strategy Seed Store And Materializer

Scope:

- Add `StrategySpecSeedStore`.
- Add API for `POST /api/strategy-seeds/materialize`.
- Wire EvidenceBundle to `StrategySpecSeedBuilder`.
- Persist seeds.
- Add review statuses.

Acceptance:

- Evidence bundle can materialize exactly one idempotent seed.
- Rejected sources cannot create seeds.
- Seed has lineage refs.
- Seed cannot request direct execution.

### PR 5: Persona Strategy Discovery

Scope:

- Add persona profile extraction.
- Add deterministic similarity scorer.
- Add strategy match API.
- Add BFF read surface.

Acceptance:

- Persona with strategy family and market preference gets ranked matches.
- Blocked matches return clear blocker reasons.
- Missing data is surfaced.
- Match can open a research ticket, not deployment.

### PR 6: LLM Proposal Governance

Scope:

- Add `SourceChangeProposal` model/store.
- Add proposal APIs.
- Add LLM proposal adapter that only writes draft proposals.
- Add operator approval/apply flow.

Acceptance:

- LLM cannot mutate source registry directly.
- Approved add/disable/retire proposal applies through audited command.
- Rejected proposal has no side effects.

### PR 7: Usage-Based Retirement And Cost/Yield Dashboard

Scope:

- Add source usage daily aggregation.
- Add low-usage recommendation rules.
- Add source health/usage BFF surface.

Acceptance:

- Low-use source produces proposal candidate.
- Critical dependency prevents retirement.
- Disabled observation window is enforced before retirement.

## 13. Testing Strategy

| Area | Test |
|---|---|
| Registry split | data source and strategy source cannot be confused |
| Lifecycle | disabled source cannot ingest |
| Schedule | archive universe skips expensive updates |
| Strategy seed | evidence bundle materializes seed with lineage |
| License | restricted source without entitlement is blocked |
| Persona discovery | deterministic match order is stable |
| Missing data | unavailable required data lowers score or blocks |
| LLM proposal | proposal has no side effect until approved |
| Retirement | low usage proposal cannot retire active dependency |
| Security | inline secrets rejected |

## 14. Design Invariants

1. Data sources and strategy seed sources are separate product objects.
2. A vendor can appear in both registries only with role-specific entries.
3. Strategy discovery never means strategy deployment.
4. Persona may propose and discover, but governance owns promotion and deployment.
5. LLM may draft source changes, but cannot apply them directly.
6. Hard delete is exceptional; retire is the default.
7. Every seed must have evidence lineage.
8. Every data-dependent strategy match must declare required data availability.
9. Every paid/restricted source must have entitlement metadata.
10. No secrets in docs, configs, evidence, or seed payloads.

## 15. Immediate Next Step

The recommended next implementation PR after this document is PR 2:

```text
Implement the registry split layer:
  DataSourceRegistry
  StrategySeedSourceRegistry
  JSONL dev stores
  lifecycle APIs
  tests proving data source and strategy seed source are not interchangeable
```

This creates the semantic boundary first. After that, FinMind/TWSE/MOPS/Yahoo data connectors and OpenAlex/GitHub strategy seed sources can be added without mixing data and strategy concepts again.
