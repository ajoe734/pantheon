# Pantheon External Data Source Management SD — 2026-08-24

Date: 2026-08-24

Inputs:

- [`CURRENT_GAP_2026-08-24.md`](CURRENT_GAP_2026-08-24.md)
- [`SA_EXTERNAL_DATA_SOURCE_MANAGEMENT_2026-08-24.md`](SA_EXTERNAL_DATA_SOURCE_MANAGEMENT_2026-08-24.md)

This software design defines the implementable phase-1 contracts for external
data source completion and Management control. It reuses the existing source
owner, BFF, Management route, search service, evidence pipeline, research
orchestrator, and memory service. It does not introduce OpenClaw-driven
development, product-hosted repository writes, unrestricted provider egress,
live orders, or capital effects.

## 1. Baseline and mandatory invariants

Implementation starts from:

| Repository | Baseline SHA |
|---|---|
| `ajoe734/pantheon` | `40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0` |
| `ajoe734/execute-plans` | `5447d2a09b5c83a4f9ee2d405f57c642913e0055` |

Workers must refresh `origin/dev` and record changed baselines before editing.

Mandatory invariants:

1. `services/source_ingestion` remains the only source mutation authority.
2. The browser calls the Pantheon BFF only; it never calls source-ingest,
   search, a vendor, or OpenClaw directly.
3. The BFF does not read source volumes or store source truth.
4. Every source mutation requires authenticated operator identity, an
   idempotency key, expected revision, typed command, and durable receipt.
5. A new source is created disabled. Creation never starts provider egress.
6. Provider credentials are referenced by ID and resolved inside the service
   boundary. Raw secret material never appears in contracts, logs, receipts,
   evidence, or browser state.
7. A bounded canary is read-only, exact-host allowlisted, record/byte/time/rate
   limited, and cannot route an order.
8. Disabled sources reject manual and scheduled execution. Retired source IDs
   are terminal.
9. Catalog/support/configured/credential-ready/validated/canary/enabled/fresh
   are separate states.
10. Access and license filters run before every search ranker.
11. Raw external data is evidence, not memory.
12. OpenClaw automation and development handoff are phase 2.

## 2. Design-unit map

| Design unit | GAPs | Main owner/result |
|---|---|---|
| SD-SRCM-01 | SRCM-G01/SRCM-G02/SRCM-G08 | source management contracts and composed truth |
| SD-SRCM-02 | SRCM-G03/SRCM-G04/SRCM-G07/SRCM-G09 | transactional source commands and receipts |
| SD-SRCM-03 | SRCM-G05/SRCM-G07 | BFF reads, commands, RBAC and service client |
| SD-SRCM-04 | SRCM-G06/SRCM-G17/SRCM-G18 | Management Data Source Control Center |
| SD-SRCM-05 | SRCM-G10/SRCM-G11/SRCM-G13/SRCM-G16 | provider definition and coverage completion |
| SD-SRCM-06 | SRCM-G12/SRCM-G13 | governed hybrid and structured-alpha search |
| SD-SRCM-07 | SRCM-G14/SRCM-G15/SRCM-G16 | evidence-to-reviewed-memory closure |
| SD-SRCM-08 | SRCM-G19/SRCM-G20 | migration, hosted acceptance and doc alignment |

## 3. SD-SRCM-01 — Contracts and composed truth

### 3.1 ConnectorDefinition projection

Add a code-owned projection module:

```text
services/source_ingestion/connector_definitions.py
```

It composes, without copying mutable state:

- `ALLOWED_PROVIDER_ADAPTERS`;
- `PROVIDER_ADAPTER_ALIASES`;
- financial catalog config templates;
- provider adapter metadata; and
- build/deployment identity.

Add schema:

```text
docs/contracts/connector_definition.schema.json
```

Contract:

```json
{
  "schema_version": "connector_definition.v1",
  "definition_id": "tw-twse-tpex-official-market",
  "adapter_token": "TaiwanOfficialMarketDatasetAdapter.records_from_payload",
  "adapter_version": "sha256-or-semver",
  "provider": "TWSE/TPEx",
  "source_kinds": ["data_source"],
  "source_types": ["market"],
  "source_classes": ["market_daily"],
  "datasets": ["tw_price_daily"],
  "auth_modes": ["none"],
  "fetch_modes": ["provider_owned_adapter"],
  "config_schema": {},
  "secret_fields": [],
  "required_pit_fields": ["event_time", "available_time", "ingest_time"],
  "default_limits": {
    "max_records": 100,
    "max_bytes": 1048576,
    "timeout_seconds": 15
  },
  "allowed_host_patterns": ["openapi.twse.com.tw", "www.tpex.org.tw"],
  "definition_state": "supported",
  "disabled_reason": null,
  "deployment_sha": "full-sha",
  "test_manifest_ref": "evidence://connector-definition/..."
}
```

Rules:

- `adapter_token` must resolve in the deployed allowlist;
- config schema must mark secrets as reference fields, never strings containing
  secret material;
- `definition_state=disabled_by_build` cannot create an instance;
- duplicate definitions or conflicting template/adapter metadata fail startup
  readiness;
- definition output is stable-sorted and fingerprinted.

Source API:

```text
GET /api/source-ingest/management/connector-definitions
GET /api/source-ingest/management/connector-definitions/{definition_id}
```

### 3.2 Canonical instance store

Wire the existing `DataSourceRegistry` and `StrategySeedSourceRegistry` into
the source service. They currently exist mainly as libraries/tests and catalog
contracts, not as the main service's persistent instance authority.

Add a store facade:

```text
services/source_ingestion/source_management_store.py
```

Backends:

- Postgres is the normal dev/staging/prod backend;
- JSONL is an explicit local/test rollback backend;
- the backend is selected by a posture variable and reported in readiness.

Extend `services/source_ingestion/pg_store.py` and `scripts/db_migrate.sh`
instead of adding a second database bootstrap mechanism.

Postgres tables, owned under `source_ingest`:

| Table | Key | Purpose |
|---|---|---|
| `data_source_instances` | source_instance_id | canonical instance/policy/current revision |
| `source_desired_states` | source_instance_id | lifecycle/config/schedule/universe intent |
| `source_command_receipts` | receipt_id | idempotent command and effect/readback |
| `source_canary_results` | canary_id | bounded activation evidence |
| `source_observed_snapshots` | source_instance_id, observed_revision | durable observed history |

The existing connector config, schedule and runtime stores remain controller
projections/operational stores during migration. They are not independently
editable by the browser.

### 3.3 DataSourceEntry v2 extension

Do not rewrite the existing v1 schema. Add an additive v2 schema and migration
adapter:

```text
docs/contracts/data_source_registry_entry.v2.schema.json
```

New required fields:

```json
{
  "schema_version": "data_source_registry_entry.v2",
  "data_source_id": "ds-twse-market-primary",
  "source_kind": "data_source",
  "definition_id": "tw-twse-tpex-official-market",
  "connector_id": "twse-market-primary",
  "provider": "TWSE",
  "provider_account_ref": null,
  "source_class": "market_daily",
  "datasets": [],
  "markets": ["TW"],
  "license_scope": "official_reference",
  "entitlement_tags": [],
  "allowed_use": ["research_data", "backtest_data", "monitoring"],
  "retention_policy_ref": "source-retention://official-market",
  "deletion_policy_ref": "source-deletion://official-market",
  "freshness_sla_seconds": 86400,
  "sensitivity": "public",
  "lifecycle_state": "configured_disabled",
  "revision": 1,
  "created_by": "operator-id",
  "created_at": "RFC3339",
  "updated_by": "operator-id",
  "updated_at": "RFC3339"
}
```

Legacy candidate entries are not bulk-labelled configured. They remain catalog
templates until explicitly admitted.

### 3.4 SourceDesiredState contract

Add:

```text
docs/contracts/source_desired_state.schema.json
```

```json
{
  "schema_version": "source_desired_state.v1",
  "source_instance_id": "ds-twse-market-primary",
  "revision": 3,
  "desired_lifecycle": "enabled",
  "definition_id": "tw-twse-tpex-official-market",
  "definition_deployment_sha": "full-sha",
  "connector_config": {
    "public": {},
    "secret_ref_id": null
  },
  "schedule": {
    "enabled": true,
    "cadence": "0 19 * * 1-5",
    "timezone": "Asia/Taipei",
    "jitter_seconds": 120
  },
  "universe_policy_ref": "active_universe_scheduling_policy.v1",
  "limits": {
    "max_records": 100,
    "max_bytes": 1048576,
    "timeout_seconds": 15
  },
  "allowed_hosts": ["openapi.twse.com.tw"],
  "last_command_receipt_id": "srcmd-...",
  "updated_at": "RFC3339"
}
```

`connector_config.public` is checked against the definition config schema.
Inline tokens/passwords/keys are rejected by name and value-pattern checks.

### 3.5 SourceObservedState contract

Add:

```text
docs/contracts/source_observed_state.schema.json
```

```json
{
  "schema_version": "source_observed_state.v1",
  "source_instance_id": "ds-twse-market-primary",
  "desired_revision": 3,
  "observed_revision": 9,
  "reconciliation_status": "converged",
  "effective_lifecycle": "enabled",
  "definition": {
    "definition_id": "tw-twse-tpex-official-market",
    "deployment_sha": "full-sha",
    "state": "supported"
  },
  "credential_state": "not_required",
  "validation_state": "passed",
  "canary_state": "passed",
  "health_state": "fresh",
  "freshness": {
    "last_success_at": "RFC3339",
    "watermark": "opaque",
    "age_seconds": 300,
    "sla_seconds": 86400
  },
  "last_run": {
    "ingest_run_id": "ingest-...",
    "row_count": 100,
    "rejected_count": 0,
    "evidence_bundle_id": "evbundle-...",
    "search_snapshot_id": "search-index-..."
  },
  "dlq_unresolved_count": 0,
  "quota": {},
  "usage": {},
  "dependent_refs": [],
  "reasons": [],
  "observed_at": "RFC3339"
}
```

### 3.6 Management DTO

The BFF composes definition, instance, desired, observed and policy into:

```text
docs/contracts/bff/management_data_source.v2.schema.json
```

It includes `allowedActions` calculated server-side:

```json
{
  "canValidate": true,
  "canCanary": true,
  "canEnable": false,
  "canDisable": false,
  "canResume": false,
  "canChangeSchedule": true,
  "canReplace": false,
  "canRetire": true,
  "blockedReasons": ["canary_required"]
}
```

The frontend does not infer actions from status strings.

## 4. SD-SRCM-02 — Source command engine

### 4.1 Files

Primary changes:

- `services/source_ingestion/main.py` — publish management reads/commands;
- `services/source_ingestion/source_management_store.py` — transactional store;
- `services/source_ingestion/source_management_commands.py` — command
  validation/effect orchestration;
- `services/source_ingestion/connector_definitions.py` — deployed capability;
- `services/source_ingestion/configured.py` — projection/reconciliation only;
- `services/source_ingestion/controller_worker.py` — converge desired state;
- `services/source_ingestion/pg_store.py` — Postgres persistence;
- `services/source_ingestion/registry/data_source_registry.py` — v2 entry
  compatibility;
- `services/source_ingestion/registry/proposals.py` — honest apply semantics;
- `services/source_ingestion/policy_registry.py` — management preconditions;
- `services/source_ingestion/source_health.py` — canonical observed mapping.

### 4.2 Internal command contract

Add schema:

```text
docs/contracts/source_management_command.schema.json
```

```json
{
  "schema_version": "source_management_command.v1",
  "command_id": "srcmd-uuid",
  "idempotency_key": "opaque-stable-key",
  "command_type": "create|validate|canary|enable|disable|degrade|resume|change_schedule|replace|retire",
  "source_instance_id": "ds-...",
  "expected_revision": 2,
  "actor": {
    "actor_type": "operator",
    "actor_id": "...",
    "roles": ["operator"]
  },
  "reason": "required human-readable reason",
  "parameters": {},
  "trace_id": "trace-...",
  "requested_at": "RFC3339"
}
```

Internal API:

```text
POST /api/source-ingest/management/commands
GET  /api/source-ingest/management/commands/{receipt_id}
GET  /api/source-ingest/management/sources
GET  /api/source-ingest/management/sources/{source_instance_id}
GET  /api/source-ingest/management/sources/{source_instance_id}/observations
```

Every internal mutation requires `Authorization: Bearer <service-token>`.
Controller reconciliation uses a distinct controller token/audience. Existing
direct connector lifecycle/config/schedule mutation routes become internal
compatibility routes and must apply the same service/controller authorization,
not only when a connector happens to be marked controller-owned.

### 4.3 Receipt contract

Add:

```text
docs/contracts/source_management_receipt.schema.json
```

```json
{
  "schema_version": "source_management_receipt.v1",
  "receipt_id": "srcrcp-uuid",
  "command_id": "srcmd-uuid",
  "idempotency_key_hash": "sha256",
  "source_instance_id": "ds-...",
  "command_type": "enable",
  "status": "accepted|running|succeeded|failed|rejected",
  "before_revision": 2,
  "after_revision": 3,
  "effect_refs": ["source-desired-state://ds-.../3"],
  "readback": {
    "desired_revision": 3,
    "observed_revision": 10,
    "reconciliation_status": "converged"
  },
  "failure": null,
  "actor_id": "operator-id",
  "trace_id": "trace-...",
  "service_deployment_sha": "full-sha",
  "created_at": "RFC3339",
  "completed_at": "RFC3339"
}
```

The same idempotency key plus same canonical request returns the existing
receipt. The same key with a different request is HTTP 409.

### 4.4 Command transaction

Command admission transaction:

1. authenticate BFF service identity and validate actor envelope;
2. canonicalize request and calculate fingerprint;
3. resolve existing receipt by idempotency key;
4. lock the source-instance row;
5. compare `expected_revision`;
6. resolve deployed ConnectorDefinition;
7. evaluate license, credential reference, lifecycle and dependency policy;
8. write the next desired revision and accepted receipt atomically;
9. enqueue/reconcile the requested effect;
10. update receipt after observed readback.

No desired-state revision is written for a rejected command.

### 4.5 CreateSupportedSource

`create` parameters contain the v2 instance, desired config and disabled
schedule. The command:

- requires a deployed `supported` definition;
- validates unique source/connector IDs;
- rejects inline secret material;
- writes the instance and revision 1 desired state;
- sets lifecycle `configured_disabled` and schedule disabled;
- materializes connector config without fetching;
- returns a receipt only after connector/config readback exists.

An unsupported definition returns:

```json
{
  "code": "adapter_not_supported",
  "development_need": {
    "schema_version": "source_development_need.v1",
    "reason": "adapter_not_supported"
  }
}
```

It does not create a source instance.

### 4.6 ValidateConfiguration

Validation is network-free unless a definition explicitly declares a safe
metadata-only validation endpoint. It checks:

- definition/config schema and adapter token;
- secret-ref existence/scopes through the secret broker;
- license, allowed use, entitlement and retention;
- host allowlist and redirect policy;
- schedule/universe/limits;
- PIT and output schema requirements; and
- current deployment definition identity.

The result is stored in observed state with input fingerprint and expiry.

### 4.7 RunBoundedCanary

Canary schema:

```text
docs/contracts/source_canary_result.schema.json
```

Required stages:

```text
definition_resolved
 -> credential_ready
 -> egress_policy_admitted
 -> provider_read
 -> source_normalized
 -> evidence_persisted
 -> search_refreshed
 -> governed_search_readback
 -> completed
```

Canary result:

```json
{
  "schema_version": "source_canary_result.v1",
  "canary_id": "src-canary-...",
  "source_instance_id": "ds-...",
  "definition_id": "...",
  "definition_deployment_sha": "full-sha",
  "limits": {"max_records": 10, "max_bytes": 262144, "timeout_seconds": 15},
  "allowed_hosts": ["provider.example"],
  "status": "passed|partial|failed",
  "stages": [],
  "ingest_run_id": "ingest-...",
  "watermark": "opaque",
  "row_count": 10,
  "rejected_count": 0,
  "evidence_bundle_id": "evbundle-...",
  "search_snapshot_id": "search-index-...",
  "query_readback_ref": "search-result-...",
  "license_scope": "vendor",
  "entitlement_tags": ["research"],
  "started_at": "RFC3339",
  "completed_at": "RFC3339"
}
```

`partial` cannot satisfy enable. Search notification/readback timeout is
partial, not pass.

### 4.8 Lifecycle effects

`enable`:

- requires current validation and passed canary fingerprints matching current
  desired config/definition;
- writes desired lifecycle enabled;
- enables schedule only if explicitly requested;
- waits for connector/schedule readback before success.

`disable`:

- accepts from configured/validated/canary/enabled/degraded states;
- disables schedule and connector execution atomically in desired state;
- active bounded runs either finish or are cancelled according to connector
  cancellation policy;
- rejects new manual and scheduled runs immediately after desired commit;
- preserves evidence/history.

`degrade`:

- records desired operator degradation or policy containment reason;
- can permit bounded manual repair while recurring schedule is disabled;
- never relaxes license or egress limits.

`resume`:

- accepts only a disabled non-retired source;
- reruns validation/canary if their fingerprint or expiry is stale;
- converges through enabled state and records a new receipt.

`retire`:

- requires disabled state;
- rejects active dependent strategies/personas without a migration/disposition;
- disables connector/schedule permanently for that instance ID;
- retains evidence and lineage under retention/deletion policy.

### 4.9 Proposal correction

Existing `SourceChangeProposal` remains useful for phase-2 suggestions and
manual planning. Correct semantics:

```text
approved
 -> executing (new nonterminal status or equivalent execution record)
 -> applied only after source command receipt status=succeeded
```

`apply` without a successful typed effect receipt is rejected. A caller-supplied
free-form `change_ref` cannot prove mutation.

Add proposal types when phase 2 begins, not as phase-1 UI commands:

- `enable_source`;
- `resume_source`;
- `change_credential_ref`;
- `run_source_canary`; and
- `request_connector_development`.

## 5. SD-SRCM-03 — BFF management facade

### 5.1 Files

- extend `services/control-plane/bff/console_gap/datasources.py`;
- extend `services/control-plane/bff/source_search_ops_client.py` or split a
  focused `source_management_client.py` when the client exceeds one domain;
- extend BFF contracts under `services/control-plane/bff/console_gap/contracts.py`;
- update `services/control-plane/bff/contract_snapshots/backend_routes_manifest.json`;
- add focused BFF contract/client/RBAC/idempotency tests.

The existing read route remains compatible:

```text
GET /bff/management/data-sources
```

It returns v2 rows when available and an explicit degraded legacy projection
during migration. It must not merge inferred persona-fleet status into a v2
row without labelling the field source.

### 5.2 BFF routes

```text
GET  /bff/management/data-sources
GET  /bff/management/data-sources/catalog
GET  /bff/management/data-sources/{source_instance_id}
GET  /bff/management/data-sources/{source_instance_id}/runs
GET  /bff/management/data-sources/{source_instance_id}/receipts

POST /bff/management/data-sources
POST /bff/management/data-sources/{source_instance_id}/actions/validate
POST /bff/management/data-sources/{source_instance_id}/actions/canary
POST /bff/management/data-sources/{source_instance_id}/actions/enable
POST /bff/management/data-sources/{source_instance_id}/actions/disable
POST /bff/management/data-sources/{source_instance_id}/actions/degrade
POST /bff/management/data-sources/{source_instance_id}/actions/resume
PUT  /bff/management/data-sources/{source_instance_id}/schedule
POST /bff/management/data-sources/{source_instance_id}/actions/replace
POST /bff/management/data-sources/{source_instance_id}/actions/retire
GET  /bff/management/source-commands/{receipt_id}
```

Do not overload `/api/v1/operator/source/ops`; that route remains operational
health/DLQ/frontier/search-index management.

### 5.3 BFF command admission

All writes require:

- authenticated `operator` or `admin` role;
- command-specific `allowedActions`;
- `X-Idempotency-Key`;
- `expectedRevision` except create;
- non-empty reason;
- explicit confirmation for enable, replace and retire;
- real-write frontend profile; and
- service-authenticated BFF-to-source call.

The BFF returns HTTP 202 with the source receipt projection. It never returns a
synthetic success when source-ingest is unavailable.

Error mapping:

| Source error | BFF status/code |
|---|---|
| source/definition not found | 404 `RESOURCE_NOT_FOUND` |
| duplicate ID/idempotency mismatch | 409 `RESOURCE_CONFLICT` |
| stale expected revision | 409 `STALE_REVISION` |
| missing role/confirmation | 403 `PRECONDITION_FAILED` |
| validation/canary prerequisite | 412 `PRECONDITION_FAILED` |
| source service unavailable | 503 `DEPENDENCY_UNAVAILABLE` |
| provider timeout during canary | accepted receipt ending failed/partial |

### 5.4 Read envelope and freshness

List/detail meta includes:

- BFF snapshot time;
- source service/deployment identity;
- definition catalog fingerprint;
- desired/observed snapshot times;
- page-level degradation; and
- exact fields served from legacy projection, if any.

Empty configured sources is a valid empty state only when the source service is
reachable and authoritative. Missing/unreachable remains unavailable.

## 6. SD-SRCM-04 — Execute Plans Management UI

### 6.1 Existing route and files to retain

Retain:

- `src/App.tsx` route `/management/data-sources`;
- `src/management/pages/oversight/DataSourceManagement.tsx` entry component;
- `src/management/navigation/managementRouteManifest.ts` navigation;
- `src/lib/bff-v1/paths.ts` path ownership;
- `src/lib/bff-v1/managementConsoleReads.ts` strict live reads;
- current translations and tests.

Refactor large page responsibilities into:

```text
src/management/pages/oversight/dataSources/
  DataSourceControlCenter.tsx
  DataSourceInstancesTable.tsx
  DataSourceCatalogPanel.tsx
  DataSourceDetailDrawer.tsx
  DataSourceAddWizard.tsx
  DataSourceCommandDialog.tsx
  DataSourceRunsPanel.tsx
  DataSourceReceiptPanel.tsx
  dataSourceModels.ts
  dataSourceActions.ts
```

`DataSourceManagement.tsx` remains the route-level composition/export to avoid
route churn.

### 6.2 Client module

Add a focused client:

```text
src/lib/bff-v1/managementDataSources.ts
```

It owns v2 types, adapters and commands. Reads use strict-live envelopes.
Writes use the existing real-write transport/gate and must never fall back to
mock success.

Command function shape:

```ts
type SourceCommandInput = {
  sourceInstanceId: string;
  expectedRevision: number;
  reason: string;
  confirmation?: boolean;
  parameters?: Record<string, unknown>;
  idempotencyKey: string;
};
```

### 6.3 Page states

The page renders distinct states:

- loading;
- authoritative empty;
- unavailable;
- degraded legacy projection;
- current v2 data;
- command pending;
- command succeeded awaiting/readback converged;
- command failed with typed corrective action;
- stale revision requiring refresh.

It does not replace missing fields with `nan`, infer credentials from a generic
status string, or derive allowed actions in the browser once v2 is available.

### 6.4 Instances table

Columns:

```text
source/provider
support/deployment
desired lifecycle
observed health/freshness
credential/license
schedule/watermark
latest run/search
consumers/cost
actions
```

The existing evidence link remains. Row action visibility comes only from
`allowedActions`. Disabled actions show BFF-provided reasons.

### 6.5 Add wizard

Wizard steps and validation mirror the SA. The final create command creates a
disabled source only. “Create and enable” is forbidden.

The secret step accepts/selects a secret reference ID and required scope. It
does not offer a raw secret input unless a separate existing secret-management
surface owns secure creation; even then this page receives only the resulting
reference.

Unsupported connector handling renders the phase-1 development-need artifact
as downloadable/copyable evidence. It does not call Management AI or OpenClaw.

### 6.6 Command UX

- validation and canary show exact limits and no-order statement;
- enable shows schedule/egress/definition/canary preconditions;
- disable is immediately available to authorized operators and requires a
  reason;
- resume shows whether validation/canary will rerun;
- replace shows dependent-consumer migration;
- retire requires typed confirmation and displays terminal semantics;
- pending commands poll the receipt endpoint and then reload detail;
- a reload must reproduce the source-owner state, not local optimistic state.

### 6.7 Frontend tests

Unit/component tests cover:

- v2 DTO adaptation and legacy degraded projection;
- all page states;
- action visibility from allowedActions;
- create-disabled wizard;
- no raw secret fields;
- pending/success/failure/stale revision;
- duplicate-click idempotency;
- real-write-off disables commands without fake success;
- keyboard/focus/accessibility for wizard, drawer and dialogs;
- responsive table/detail behavior.

Add hosted E2E:

```text
e2e/30-management-data-source-control.spec.ts
```

It uses existing auth/BFF helpers and no route mocks for the acceptance path.

## 7. SD-SRCM-05 — Provider completion

### 7.1 Definition/adapter/catalog reconciliation

Add a build-time/focused test that joins:

```text
financial catalog entry
 -> config template
 -> definition
 -> provider adapter allowlist
 -> expected normalized schema
```

Fail when:

- a candidate/supported template references no adapter;
- an adapter lacks a definition/template when intended for operator use;
- adapter token/config keys conflict;
- disabled reason is missing;
- source class/type mapping changes silently; or
- a social source is projected as news.

### 7.2 TDCC

Extend the Taiwan official-source implementation or add a focused adapter under
`services/source_ingestion/connectors/` if protocol isolation warrants it.

Acceptance fields:

- weekly publication identity;
- shareholding distribution buckets and symbol mapping;
- publication/available/ingest time;
- correction/republication handling;
- official license/allowed use;
- weekly watermark and backfill window;
- normalized dataset and evidence/search canary.

### 7.3 TAIFEX

Implement futures/options chip context with:

- market/trading date and contract identity;
- futures OI/participant flow and options put/call context;
- publication/available/ingest time;
- contract roll and calendar policy;
- no per-symbol archive fanout where the catalog forbids it;
- daily watermark and evidence/search canary.

### 7.4 Social

Do not implement a generic arbitrary scraper. Select an admitted provider/API
and add a dedicated `SOCIAL` DataSourceClass.

Required policies:

- account/post/thread identity and deletion/tombstone propagation;
- bot/spam/moderation metadata;
- terms, retention and full-text rights;
- public/private community scope;
- sentiment output labelled derived, model/version referenced;
- no direct execution use.

### 7.5 External Alpha DB

Add:

```text
services/source_ingestion/connectors/alpha_db.py
docs/contracts/alpha_signal_record.schema.json
```

Contract:

```json
{
  "schema_version": "alpha_signal_record.v1",
  "alpha_vendor_id": "vendor",
  "signal_id": "signal",
  "signal_version": "v1",
  "field_schema_version": "v1",
  "universe": ["US_EQUITY"],
  "entity_id": "security-master-id",
  "event_time": "RFC3339",
  "as_of_time": "RFC3339",
  "available_time": "RFC3339",
  "ingest_time": "RFC3339",
  "values": {},
  "units": {},
  "currency": null,
  "corporate_action_policy": "provider_adjusted|pantheon_adjusted|raw",
  "survivorship_policy": "point_in_time",
  "license_scope": "restricted",
  "allowed_use": ["research", "experiment"],
  "entitlement_tags": [],
  "provider_record_ref": "opaque",
  "body_hash": "sha256"
}
```

At least one selected real provider must pass current-host bounded canary before
the source family is complete. The existing `example-alpha-db` remains a test
fixture and is never shown as configured/live.

### 7.6 Provider evidence packet

Store acceptance evidence under the existing task/evidence workflow, not as
runtime truth. Runtime truth remains the source management store and live
readback.

Per-provider packet fields:

- exact source/definition/service/deployment identity;
- license/entitlement decision;
- secret-ref readiness only;
- bounded limits and allowed hosts;
- provider HTTP/result metadata with sensitive fields redacted;
- row/reject/schema/PIT/watermark/freshness;
- evidence/search readback;
- disable/rollback result; and
- timestamp and environment.

## 8. SD-SRCM-06 — Governed search and structured alpha

### 8.1 Existing files

Extend:

- `services/search/filters.py`;
- `services/search/gateway.py`;
- `services/search/retriever.py`;
- `services/search/index_adapter.py`;
- `services/search/index_pipeline.py`;
- `services/search/index_store.py` and `pg_store.py`;
- `services/search/main.py`;
- search posture/readiness/tests.

Add focused modules only where behavior is distinct:

```text
services/search/structured_alpha.py
services/search/hybrid_retriever.py
```

### 8.2 SearchRequest v2

Add a versioned request instead of changing v1 meaning silently:

```json
{
  "schema_version": "governed_search_request.v2",
  "query": "momentum quality",
  "retrieval_mode": "keyword|full_text|semantic|hybrid|structured_alpha",
  "actor_ref": "...",
  "persona_id": "...",
  "workspace_id": "...",
  "role_refs": ["researcher"],
  "environment": "paper",
  "purpose": "research",
  "filters": {
    "source_types": [],
    "license_scopes": [],
    "sensitivity": [],
    "capital_pool_scope": [],
    "event_time_gte": null,
    "event_time_lte": null,
    "available_time_lte": "RFC3339",
    "asset_class": [],
    "strategy_id": null
  },
  "structured_alpha": null,
  "top_k": 10,
  "require_citations": true,
  "trace_id": "..."
}
```

V1 `time_window` is translated into explicit event bounds or rejected when
ambiguous. It is never accepted and ignored.

### 8.3 Pre-retrieval filter plan

The gateway builds an immutable filter plan and applies it to the durable
candidate query before ranking. Semantic retrieval must query only authorized
document/vector IDs; it cannot retrieve globally and filter afterward.

Response audit includes accepted filters and rejection counts grouped by:

- source type;
- environment;
- access/license/entitlement;
- persona/workspace/role;
- sensitivity/capital pool;
- event/available time; and
- missing citation/evidence.

### 8.4 Hybrid retrieval

- Postgres full-text search is the lexical production baseline;
- vector retrieval requires an explicit configured embedding model/version and
  durable vector backend;
- hybrid ranking uses a versioned, testable calibration method such as reciprocal
  rank fusion;
- result includes component scores and ranker version;
- unsupported semantic/hybrid mode returns capability unavailable;
- keyword retrieval remains available as an honest explicit mode.

### 8.5 Structured alpha query

Add:

```text
docs/contracts/alpha_rule_query.schema.json
```

Use a constrained AST, never executable Python/SQL text:

```json
{
  "schema_version": "alpha_rule_query.v1",
  "dataset_ref": "alpha-dataset-version",
  "universe": ["US_EQUITY"],
  "as_of": "RFC3339",
  "rule": {
    "op": "and",
    "args": [
      {"op": "gte", "field": "quality_score", "value": 0.8},
      {"op": "gt", "field": "momentum_20d", "value": 0}
    ]
  },
  "sort": [{"field": "quality_score", "direction": "desc"}],
  "limit": 50
}
```

Allowed operations and fields come from the dataset's versioned field schema.
Reject unknown fields/operators, type/unit mismatch, excessive complexity,
future availability, unentitled datasets, and unbounded result limits.

Persist a result snapshot containing dataset/query fingerprints, cutoff,
matched entity IDs/values, citations, license and provider quota/cost receipt.

## 9. SD-SRCM-07 — Evidence-to-reviewed-memory

### 9.1 Research completion event

Add a research-owned transactional outbox record only after:

- task/run is terminal completed;
- artifact/registry writeback quality gate passes;
- result has evidence/citation and dataset/version lineage;
- reviewer/policy state permits publication; and
- license allows derived-memory use.

Recommended files:

```text
services/research/research_memory_outbox.py
services/research/memory_writeback_worker.py
```

Extend `services/research/main.py` at the terminal reviewed result boundary.
Do not write memory from source ingest or search.

### 9.2 Memory event

Extend memory enums/contracts with a research-owned event:

```json
{
  "source_event_type": "research_finding_published",
  "source_event_id": "research-task-or-run-id",
  "write_authority": "research-svc",
  "sponsor_persona_id": "persona-id",
  "summary": "reviewed finding",
  "confidence": 0.0,
  "evidence_refs": [],
  "dataset_refs": [],
  "license_scope": "...",
  "allowed_use": [],
  "supersedes": [],
  "contradicts": [],
  "expires_at": null,
  "trace_id": "..."
}
```

`services/memory/learn_feedback_writeback.py` maps it to:

- PersonaMemory `strategy_lesson`; and
- InstitutionalMemory `research_finding`.

Writeback is idempotent by authority/event identity. Failed delivery retries
through the research outbox; it does not make the research result un-completed,
but Management shows memory delivery pending/degraded.

### 9.3 Retrieval/influence proof

When a later research task retrieves memory, record:

- retrieval request/snapshot;
- selected memory/evidence refs;
- counter-evidence query/result;
- how the item influenced or did not influence the hypothesis;
- model/ranker/version; and
- resulting experiment/seed ref.

Inspiration edges use actual recorded influence. A lineage fallback may state
`influence_unknown`; it may not assign `1.0` merely because an upstream edge
exists.

## 10. SD-SRCM-08 — Migration and rollout

### 10.1 Store migration

1. Create Postgres source-management tables idempotently.
2. Project current code definitions and verify catalog/adapter consistency.
3. Import actual configured connector instances as `configured_disabled` or
   their verified current desired state; do not import catalog-only entries.
4. Import schedules and public connector config; redact/reject inline secrets.
5. Capture observed health/watermark snapshots with `source=legacy_projection`.
6. Compare the v2 composed list to existing `/bff/management/data-sources`.
7. Enable controller reconciliation from desired state in shadow/report-only
   mode.
8. Prove no unexpected connector/schedule mutation.
9. Turn on source commands for a bounded test definition.
10. Retire legacy inference only after parity and hosted acceptance.

Migration artifacts include counts, IDs, skipped catalog entries, secret
redactions, conflicts and checksums. Import is repeatable and idempotent.

### 10.2 Feature flags/posture

Suggested configuration:

```text
SOURCE_MANAGEMENT_STORE_BACKEND=postgres
SOURCE_MANAGEMENT_COMMANDS_ENABLED=0
PANTHEON_BFF_SOURCE_MANAGEMENT_COMMANDS_ENABLED=0
VITE_BFF_REAL_WRITES=false
```

Rollout sequence:

| State | Source commands | BFF commands | FE profile |
|---|---|---|---|
| schema/read shadow | off | off | read-only |
| internal test | bounded test source | off | read-only |
| candidate | bounded | operator/admin | write-enabled candidate |
| accepted normal dev | policy-controlled | available to authorized role | normal artifact per release policy |

The normal accepted FE may remain read-only if operations policy requires it;
the capability is accepted through the bounded candidate and can be exposed by
an explicit write profile.

### 10.3 Rollback

Rollback:

- turns off BFF/source command flags;
- stops desired-state reconciliation mutations;
- serves the existing read-only Management page;
- leaves all new/changed sources disabled unless the previous accepted desired
  state explicitly proves otherwise;
- preserves receipts, observed snapshots, evidence and lineage; and
- never restores raw secrets or deletes provider data.

## 11. Tests and verification

### 11.1 Source unit tests

Positive:

- definition projection joins adapters/templates;
- create supported source produces revision 1 disabled state;
- validate/canary/enable/disable/resume sequence;
- schedule change and controller convergence;
- idempotent same-key replay;
- current canary produces evidence and search readback;
- retirement after dependency disposition.

Negative:

- unsupported/disabled definition;
- inline secret material;
- missing/wrong secret scopes;
- stale expected revision;
- idempotency key reused with changed body;
- enable without current validation/canary;
- canary host redirect outside allowlist;
- max record/byte/time/rate violation;
- search refresh/readback failure returns partial;
- disabled manual/scheduled run;
- resume retired source;
- retire with active dependency;
- wrong service/controller token;
- proposal apply without successful effect receipt.

### 11.2 Store/concurrency tests

- two commands on the same expected revision: one succeeds, one conflicts;
- crash after desired commit before reconciliation resumes safely;
- crash after provider read does not duplicate source records/evidence;
- receipt and desired revision commit atomically;
- Postgres restart preserves receipts and idempotency;
- JSONL local mode has identical command semantics;
- tenant/environment isolation if stores are shared.

### 11.3 BFF tests

- list/detail/catalog envelope and degradation;
- operator/admin allowed, reader/persona denied for writes;
- confirmation and idempotency required;
- service token forwarded, operator token not forwarded as provider auth;
- expected revision conflicts mapped correctly;
- source error/timeout mapping;
- no synthetic success or fixture fallback;
- allowedActions derived server-side;
- raw secret-like fields redacted/rejected;
- exact receipt polling/readback.

### 11.4 Frontend tests

As defined in section 6.7, plus:

- no controls when BFF commands are disabled;
- unsupported definition produces development need only;
- action failures survive reload without optimistic state;
- desired/observed divergence is visible;
- stale/fresh uses source SLA, not page request time;
- retired source history remains accessible.

### 11.5 Search tests

- v1 time window translation and rejection of ambiguity;
- available-time as-of cutoff;
- each mandatory access filter before ranking;
- semantic index receives authorized IDs only;
- explicit mode unavailable response;
- hybrid component scores/ranker version;
- structured alpha valid AST;
- unknown field/operator/type/unit/complexity rejected;
- PIT/survivorship/license/entitlement and result snapshot;
- query replay by dataset/index fingerprint.

### 11.6 Memory tests

- raw SourceRecord cannot call memory writer;
- unreviewed research result cannot write memory;
- reviewed result creates persona and institutional entries;
- event replay is idempotent;
- license/allowed-use/expiry/supersession propagation;
- delivery retry/outbox readback;
- subsequent retrieval records selected and counter-evidence refs;
- inspiration fallback does not claim a synthetic influence weight.

### 11.7 Hosted acceptance

Prerequisites:

- exact BFF source SHA from `/bff/version` equals deployment manifest BFF SHA;
- exact FE SHA equals manifest FE SHA;
- source service reports expected deployment definition SHA;
- operator-live candidate has real writes enabled only for acceptance;
- test connector has no order/capital route;
- external egress allowlist contains only the test provider host.

Required hosted journeys:

1. public/no-secret source create-disabled through browser;
2. validate and bounded canary;
3. SourceRecord/Evidence/Search readback;
4. enable and observed convergence;
5. disable and reload persistence;
6. duplicate command idempotency;
7. unauthorized and stale-revision rejection;
8. credentialed test source with secret ref and no secret exposure;
9. provider failure/degraded UI;
10. rollback to read-only accepted artifact.

The acceptance artifact records network requests/responses with secrets
redacted, source receipts, exact identities, screenshots, and no-order
assertions. Route-mocked browser tests cannot close hosted acceptance.

## 12. File-level disposition

### Pantheon retain/extend

| Path | Disposition |
|---|---|
| `services/source_ingestion/main.py` | extend with management APIs; keep service owner |
| `services/source_ingestion/configured.py` | retain as runtime projection |
| `services/source_ingestion/controller_worker.py` | extend desired-state reconciliation |
| `services/source_ingestion/provider_adapters.py` | retain code allowlist and reconcile definitions |
| `services/source_ingestion/financial_source_catalog.py` | retain templates; never live truth |
| `services/source_ingestion/registry/data_source_registry.py` | wire into service and extend v2 compatibility |
| `services/source_ingestion/registry/strategy_seed_source_registry.py` | wire for managed research sources |
| `services/source_ingestion/registry/proposals.py` | correct applied effect semantics |
| `services/source_ingestion/pg_store.py` | extend durable source management storage |
| `services/search/*` | extend governed modes/filter execution |
| `services/research/main.py` | emit reviewed research memory event |
| `services/memory/learn_feedback_writeback.py` | accept research-owned reviewed event |
| `services/control-plane/bff/console_gap/datasources.py` | expand canonical route family |
| `services/control-plane/bff/source_search_ops_client.py` | retain ops; split management client if needed |

### Pantheon add

```text
services/source_ingestion/connector_definitions.py
services/source_ingestion/source_management_store.py
services/source_ingestion/source_management_commands.py
services/search/structured_alpha.py
services/search/hybrid_retriever.py
services/research/research_memory_outbox.py
services/research/memory_writeback_worker.py
docs/contracts/connector_definition.schema.json
docs/contracts/data_source_registry_entry.v2.schema.json
docs/contracts/source_desired_state.schema.json
docs/contracts/source_observed_state.schema.json
docs/contracts/source_management_command.schema.json
docs/contracts/source_management_receipt.schema.json
docs/contracts/source_canary_result.schema.json
docs/contracts/alpha_signal_record.schema.json
docs/contracts/alpha_rule_query.schema.json
```

Names may be adjusted to existing repository conventions, but ownership and
contract boundaries must remain.

### Execute Plans retain/extend

| Path | Disposition |
|---|---|
| `src/management/pages/oversight/DataSourceManagement.tsx` | retain route entry, compose new control center |
| `src/lib/bff-v1/managementConsoleReads.ts` | retain generic canonical reads |
| `src/lib/bff-v1/paths.ts` | add route paths |
| `src/lib/v5/management/systemDataSources.ts` | migrate from inferred legacy model to v2 adapter |
| `src/i18n/locales/en-US.ts` / `zh-TW.ts` | add control/receipt/error strings |
| `src/management/pages/oversight/DataSourceManagement.test.tsx` | extend migration/route tests |

### Execute Plans add

```text
src/lib/bff-v1/managementDataSources.ts
src/management/pages/oversight/dataSources/*
e2e/30-management-data-source-control.spec.ts
```

No frontend source is copied into Pantheon.

## 13. Documentation updates after implementation

Update, rather than silently supersede:

- `docs/03/SD-03_source_knowledge_evidence.md`;
- `docs/04/pantheon_sa/SA-16_data_search_external_source_gap_analysis.md`;
- `docs/04/pantheon_data_strategy_source_design_2026-06-09/`;
- Management BFF contracts and screen docs; and
- `execute-plans` route/user documentation.

Required terminology:

```text
target          design/intended capability
supported       deployed adapter definition exists
configured      source instance exists
credentialed    secret ref resolves for required scopes
validated       config/policy validation passed
canary-passed   bounded current-deployment downstream proof passed
enabled         desired execution permission
fresh           observed watermark within source SLA
live            current environment proof with exact identities
```

Remove legacy `front-ai-trading-system` references from current active design
sections; retain them only when explicitly labelled historical.

## 14. Phase-1 completion checklist

- [ ] Contracts in SD-SRCM-01 are versioned and validated.
- [ ] Source-instance Postgres authority and reconciliation are active.
- [ ] Create-disabled/validate/canary/lifecycle/schedule/retire commands return
      durable receipts.
- [ ] Every mutation is service-authenticated, RBAC-gated and idempotent.
- [ ] Existing Management page is the working control center.
- [ ] TDCC/TAIFEX and required P0 source coverage have current-host proof.
- [ ] Social and External Alpha DB are either genuinely admitted or honestly
      unavailable; examples are not live claims.
- [ ] Search applies as-of/time/access filters and structured alpha rules.
- [ ] Real external evidence reaches reviewed research and memory with license
      lineage.
- [ ] Exact FE/BFF/source identities and hosted browser journeys pass.
- [ ] Normal rollback returns to read-only without enabling or deleting data.
- [ ] OpenClaw development remains phase 2 and outside product write authority.
