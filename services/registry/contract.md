# Strategy and Model Registry Contract

**Task:** REG-001 foundation, updated by REG-004  
**Owner:** Codex  
**Reviewer:** Claude  
**Status:** DRAFT — canonical registry semantics now split artifact governance from deployment stage

---

## 1. Purpose

The registry is the governed source of truth for every artifact that may eventually influence execution.

It exists so that:

- strategy evolution is versioned
- lineage is traceable
- artifact governance maturity is explicit
- rollback defaults are explicit
- LEAN execution never loads an artifact that is not governance-approved

`REG-004` changes one important rule from the earlier registry draft:

- `artifact_state` is the registry lifecycle
- `deployment_stage` is a separate deployment/runtime concern

The registry may reference deployment facts, but it must not collapse deployment stage into the
artifact lifecycle again.

Machine-readable entry schema:

- `services/registry/registry_entry_schema.json`

---

## 2. Artifact Types

The registry must support more than one artifact class.

| Artifact type | Example |
|---|---|
| `strategy_spec` | normalized StrategySpec from research ingestion |
| `model_artifact` | trained model weights or bundle |
| `behavior_policy` | behavior-cloned trader policy candidate from governed imitation datasets |
| `feature_set` | versioned feature definitions |
| `prompt_bundle` | persona optimization output such as DSPy program |
| `signal_snapshot` | versioned signal or allocation snapshot |
| `execution_bundle` | deployable package that execution consumes |
| `evaluation_result` | evaluator-produced advisory assessment payload |
| `critique_result` | critic-produced rationale and risk assessment payload |
| `optimizer_result` | optimizer-run provenance artifact (EV-002) |

Not every artifact is executable, but every artifact uses the same governance vocabulary.
Not every artifact traverses every deployment stage.

- executable artifacts may be deployed to `paper`, `canary`, or `live` only after they are `approved`
- `behavior_policy` artifacts are governed learned policies; they must remain non-live until normal evaluation, approval, deployment planning, and runtime binding gates complete
- reference artifacts such as `evaluation_result`, `critique_result`, and `optimizer_result` are governed but non-executable, and in v1 normally remain `candidate` or `approved` until superseded or explicitly `retired`

---

## 3. Canonical Artifact State

The registry lifecycle is now:

| `artifact_state` | Meaning |
|---|---|
| `draft` | created but not yet replication-ready |
| `candidate` | passed normalization or replication gate and is ready for governance review |
| `approved` | governance approved the artifact for possible deployment |
| `retired` | no longer valid for approval, deployment planning, or new loading |

### Allowed artifact-state transitions

```text
draft -> candidate
candidate -> approved
approved -> retired
candidate -> retired
draft -> retired
```

Rules:

- `approved` replaces the older registry meaning that was previously encoded as `paper` or `live`
- rollback is not an `artifact_state` transition
- rollback means re-binding runtime to a different already `approved` artifact through deployment/runtime objects

---

## 4. Deployment Stage Is Separate

Pantheon tracks actual deployment separately from registry state.

Canonical `deployment_stage` values are:

- `none`
- `paper`
- `canary`
- `live`
- `frozen`

Ownership rules:

- registry owns `artifact_state`
- governance/deployment own `DeploymentPlan`
- execution owns `RuntimeBinding`
- any deployment-stage summary attached to a registry view is derived and non-authoritative

Consequences:

- `paper`, `canary`, and `live` no longer appear in the registry lifecycle enum
- an artifact may be `approved` while still at deployment stage `none`
- `frozen` is a deployment/runtime condition, not a registry state

---

## 5. Registry Entry Model

Each registry entry must contain these fields:

| Field | Required | Description |
|---|---|---|
| `registry_id` | yes | stable unique id for the entry |
| `artifact_type` | yes | one of the artifact types in §2 |
| `strategy_id` | yes | stable strategy family identifier |
| `version` | yes | semantic version for the artifact entry |
| `artifact_state` | yes | `draft`, `candidate`, `approved`, `retired` |
| `lineage` | yes | source runs, parent entries, or upstream artifacts |
| `storage_ref` | yes | where the artifact bytes or payload live |
| `checksum` | yes | integrity check for the artifact payload |
| `producer_run_id` | no | training, optimization, or ingest run id |
| `evaluation_summary` | no | evaluator outputs and scores |
| `approval_decision_id` | no | canonical approval object ref once `GOV-001` lands |
| `approved_at` | no | when the artifact entered `approved` state |
| `approver` | no | temporary compatibility actor hint until `ApprovalDecision` is first-class |
| `rollback_target` | no | prior approved version safe to rebind during deployment rollback |
| `deployment_summary` | no | derived read-model view of current stage / binding refs; not authoritative |
| `metadata` | no | non-governing supplemental fields |

Notes:

- once `GOV-001` lands, `approval_decision_id` becomes the canonical authority for `approved`
- `deployment_summary` may cache the latest deployment read model, but registry writers must not treat it as source truth

### Suggested `deployment_summary` shape

If a read model is embedded with a registry entry, the summary should be treated as read-only and may contain:

| Field | Required | Description |
|---|---|---|
| `current_stage` | no | `none`, `paper`, `canary`, `live`, or `frozen` |
| `deployment_plan_id` | no | current or last applied `DeploymentPlan` |
| `runtime_binding_id` | no | active `RuntimeBinding` when present |
| `last_transition_at` | no | last deployment-stage transition time |

---

## 6. Lineage Requirements

`lineage` is required because the registry is not just storage. It is audit and causality.

Minimum lineage subfields:

| Field | Required | Description |
|---|---|---|
| `parent_registry_ids` | no | direct parents if this entry derives from earlier versions; mandatory for noninitial StrategySpec revisions |
| `source_run_ids` | no | training / optimization / replication runs |
| `source_dataset_refs` | no | dataset or feature store references |
| `source_strategy_spec_id` | no | originating StrategySpec when applicable |

If an artifact reaches `approved`, lineage must not be empty. Every noninitial StrategySpec revision must declare explicit caller parent identity (`parent_registry_ids` in lineage) naming an existing StrategySpec entry for that strategy family, and must advance from the current latest revision (stale parent fails with 409 Conflict). Content checksum alone is not revision CAS.

---

## 7. Execution Projection and Deployment View

`EX-001` still needs a LEAN-facing metadata document in Object Store, but the canonical target
projection is now deployment-aware.

That `metadata.json` remains a projection of registry + deployment truth, not a separate source of truth.

### Canonical target fields for loader-facing metadata

| Source of truth | Projected field |
|---|---|
| registry `strategy_id` | `strategy_id` |
| registry `version` | `version` |
| registry `artifact_state` | `artifact_state` |
| deployment/runtime read model | `deployment_stage` |
| registry `checksum` | `checksum` |
| registry `approved_at` | `approved_at` |
| registry `lineage` | `lineage` |

### Canonical loader-facing rules

The projection must make these checks possible without extra registry calls:

- runtime loading requires `artifact_state=approved`
- `paper` mode may only load artifacts with `deployment_stage=paper`
- `canary` mode may only load artifacts with `deployment_stage=canary`
- `live` mode may only load artifacts with `deployment_stage=live`
- `candidate`, `retired`, `none`, and `frozen` must be rejected for new execution loads

### Compatibility window

Current `REG-002` / `REG-003` / `EX-001` code paths still emit legacy `lifecycle_state` and
`promotion_state` fields. During the migration window:

- `lifecycle_state` and `promotion_state` are legacy compatibility fields only
- new registry contracts must treat `artifact_state` / `deployment_stage` as canonical
- follow-on tasks `GOV-001`, `DEP-001`, and execution-side contract updates will migrate the code path to the new projection envelope

### v1 Object Store key continuity

To avoid breaking the current path shape, the canonical Object Store keys remain:

- `openclaw/registry/{strategy_id}/{version}/metadata.json`
- `openclaw/registry/{strategy_id}/{version}/artifact.bin`

---

## 8. Minimal Operations

The storage backend is still open, but the logical operations are not.

| Operation | Description |
|---|---|
| `register(entry)` | create a new `draft` or `candidate` entry |
| `get(registry_id)` | read one entry |
| `list_by_strategy(strategy_id)` | enumerate versions within a strategy family |
| `advance_artifact_state(registry_id, target_state, approver?, approval_decision_id?, command_key?)` | transition an entry through governed artifact-state checks and retain the canonical decision link when approving; `command_key` makes an identical retry an idempotent replay of the original committed transition instead of re-running it (see below) |
| `update_metadata(registry_id, expected_metadata, new_metadata, command_key?)` | allowed operator metadata update with CAS: fails with a conflict when `expected_metadata` no longer matches the durable entry; `command_key` makes an identical retry an idempotent no-op replay |
| `resolve_latest_approved(strategy_id)` | return the newest approved entry for a strategy |
| `resolve_deployment_view(strategy_id)` | return the derived deployment-stage view from deployment/runtime objects |

`resolve_deployment_view()` is a composed read path, not a registry-only write authority.

`update_metadata()` mutates only the operator-facing `metadata` record kind — it can never fabricate
or upgrade a validated StrategySpec or `artifact_state`. It is the `PATCH /api/registry/entries/{registry_id}/metadata`
HTTP endpoint (REGISTRY-STRATEGY-UNIFIED-CONTRACT-001), backed by a real Postgres CAS commit in the
same transaction as its idempotent command receipt when `command_key` is supplied. See
`services/registry/first_release_contract.json` for the frozen capability matrix and
`services/registry/command_contract.py` for the canonical Strategy-action-to-owner mapping (Registry
owns this metadata update; review/paper-promotion/activation/pause/archive belong to other owners and
must not be relabeled as Registry operations).

#### Command-receipt durability mechanism (not a separate outbox)

Every owned mutation that accepts an idempotency key (`update_metadata`'s `command_key`,
`advance_artifact_state`'s `command_key`, and the generic-create `Idempotency-Key` header on
`POST /api/registry/entries`) commits its receipt in the **same Postgres transaction** as the state
write it records — one row in `registry.command_receipts`, written via the same shared connection
(`PostgresJsonOwnerStore.transaction()`) as the `registry.entries` row it mutates
(`PostgresRegistryStore.commit_metadata_cas` / `commit_artifact_state_cas` / `create_with_receipt`).
This is deliberately **not** a separate outbox/prepare-activate-reconcile protocol: there is no
second event table, no background relay, and no "pending" intermediate state to reconcile. The crash
safety property this buys is narrower but concrete: a crash between "entry committed" and "response
sent to the caller" is safe, because a replay of the same `command_key` re-reads the already-committed
receipt row (bound to that exact request) instead of re-running the mutation or silently no-op'ing —
and a crash *during* the transaction (before either row commits) rolls back atomically, leaving neither
the entry mutation nor the receipt reservation behind. `advance_artifact_state`'s replay path
additionally never re-runs the transition-legality/lineage business-rule check on a replay (that check
only runs on the genuinely-fresh path, after the store has already ruled out a replay) — re-checking it
against the entry's *current* (already-post-transition) state would otherwise spuriously reject a
legitimate replay as a "forbidden transition".

Receipt keys (`PostgresRegistryStore.receipt_key`) are scoped by tenant + actor + `registry_id` +
**command type** (`"metadata"` / `"advance"` / `"create"`) + `command_key` — the command type is
folded into the framed identity so the same client-chosen `command_key` value reused for both a
metadata-CAS call and an `advance` on the same `registry_id`/tenant/actor can never land on the same
receipt row, regardless of whether their request digests happen to differ.

Every method that writes to both `registry.entries` and `registry.command_receipts` in one transaction
locks the **entries table first**, before ever touching receipts (`PostgresJsonOwnerStore.lock_table`,
called explicitly ahead of any receipts access in `create_with_receipt`/`commit_metadata_cas`/
`commit_artifact_state_cas`; `create_if_absent`/`register_strategy_spec_revision` already insert into
entries before receipts naturally). This global ordering is required because
`PostgresJsonOwnerStore.insert_if_absent` always takes a table-level `SHARE ROW EXCLUSIVE` lock: two
unrelated, lawful concurrent requests that happened to touch the two tables in opposite orders could
deadlock in Postgres (one holding entries and waiting on receipts, the other holding receipts and
waiting on entries) rather than merely serialize.

`advance_artifact_state` requires the caller's own claimed base (`expected_artifact_state`; every
public `.../advance` route rejects an omitted value with 422) and accepts optional further narrowing
(`expected_version`/`expected_updated_at`). Each supplied field is merged onto the CAS base snapshot
before the compare-and-set, binding the write to what the caller actually believes the entry's current
state is — never only a value the store re-read fresh at request time. A stale claim fails the same 409
conflict a stale `expected_metadata` already does on `update_metadata`. `expected_artifact_state` was
made mandatory (previously optional, silently falling back to a fresh server re-read as the CAS base
when omitted) after an independent review reproduced a real-Postgres HTTP request that advanced a bound
transition, then advanced again with no expected_* field at all and still succeeded — not a caller-bound
CAS at all.

A same-`registry_id` create-if-absent replay (the StrategySpec/StrategyArtifact/AllocationPolicyArtifact
facades' "already registered, return the existing entry" path) is compared against, and now **returns**,
the entry's **original creation content** — a snapshot recorded once, in the same transaction as the
entry's first successful insert, and exposed via
`PostgresRegistryStore.get_creation_receipt`/`RegistryStore.get_creation_receipt` — not whatever the row
has mutated into since (a later `advance` or `update_metadata` call). This keeps an exact replay of the
original request succeeding even after legitimate downstream progress, and reports back exactly what
that original command committed rather than an unrelated later command's edit; the durable row itself is
never reverted, and the ordinary `GET` route always returns the live current entry.

A same-`registry_id` collision on any create-if-absent path (including
`AllocationPolicyArtifactRegisterRequest`'s caller-suppliable `registry_id`) is independently authorized
(tenant/builtin scoped, matching a `GET` of the same entry) and kind/content-matched against the caller's
own request before ever being returned — a caller cannot read another tenant's private entry, or a
different artifact kind entirely, simply by re-POSTing a guessed or known `registry_id`.

### Storage backend

Production selects `PostgresRegistryStore` (`services/registry/pg_store.py`, `REGISTRY_STORE_BACKEND=postgres`)
as the single durable write authority for StrategySpec content, immutable versions, RegistryEntry
identities and artifact-state, using `services/foundation/postgres_json_store.py`'s CAS/transaction
primitives. The in-memory `RegistryStore` (`services/registry/storage.py`) is an explicit test double
constructed directly by unit tests, never a missing-config production fallback: `REGISTRY_STORE_BACKEND`
must always be set to `memory` or `postgres` explicitly (`storage.build_registry_store` raises otherwise,
in every posture, not only an enforced staging/prod one) — `services/registry/conftest.py` sets it to
`memory` explicitly for this whole package's unit-test run.

### StrategySpec registry facade

`STRAT-002` adds a narrow StrategySpec-specific HTTP facade over the generic registry operations.
It does not create a second lifecycle or bypass the generic registry state machine.

| HTTP operation | Description |
|---|---|
| `POST /api/registry/strategy-specs` | register a `strategy_spec` artifact with required lineage plus `storage_ref`/`checksum`, or an inline StrategySpec payload from which checksum and inline storage are derived |
| `GET /api/registry/strategy-specs/{registry_id}` | read one `strategy_spec` registry entry and reject non-StrategySpec artifacts on this facade |
| `GET /api/registry/strategies/{strategy_id}/strategy-specs` | list only StrategySpec entries for a strategy family, optionally filtered by `artifact_state` |
| `POST /api/registry/strategy-specs/{registry_id}/advance` | advance a StrategySpec entry through the same `draft -> candidate -> approved -> retired` artifact-state machine |

The facade exists so source-seed and distillation workers can register StrategySpec artifacts without
supplying or trusting `artifact_type` themselves. It must still preserve:

- lineage from source seed, source run, parent registry entry, dataset, or source StrategySpec
- `storage_ref` and `checksum` on every registered StrategySpec artifact
- mandatory caller parent identity (`parent_registry_ids`) on all noninitial revisions; checksum alone cannot identify parent revisions
- identical atomic revision sequencing invariants across both the dedicated `/api/registry/strategy-specs` facade and the generic `/api/registry/entries` endpoint for all StrategySpec representations (inline or storage reference)
- the same `artifact_state` / `deployment_stage` split as the generic registry entry API

### Evolvable StrategyArtifact facade

`EVOLOOP-003` defines an executable StrategyArtifact as an additive payload
overlay on the existing `execution_bundle` artifact type. The portable payload
schema is `services/registry/strategy_artifact.schema.json`; the generic
registry envelope stores it inline at `metadata.strategy_artifact` with a
deterministic checksum. This does not add a second lifecycle or a new artifact
type.

| HTTP operation | Description |
|---|---|
| `POST /api/registry/strategy-artifacts` | validate and atomically register a `draft` or `candidate` StrategyArtifact; only the same normalized registration envelope (initial state, artifact, producer, evaluation, rollback, and supplemental metadata) is an idempotent retry, while any same-id difference fails closed |
| `GET /api/registry/strategy-artifacts/{registry_id}` | read only an `execution_bundle` carrying the StrategyArtifact overlay |
| `GET /api/registry/strategies/{strategy_id}/strategy-artifacts` | list StrategyArtifact revisions for a family, optionally filtered by `artifact_state` |
| `POST /api/registry/strategy-artifacts/{registry_id}/mutate` | create a new `candidate` id/version by changing only declared controls and recording direct-parent plus producing-run lineage |
| `POST /api/registry/strategy-artifacts/{registry_id}/advance` | use the same governed `draft -> candidate -> approved -> retired` lifecycle |

Checked-in built-in registration requests are registered idempotently through
`RegistryService` during the FastAPI lifespan startup and guarded again at
request-time after a test/store reset. Atomic `create_if_absent` prevents
concurrent same-id mutations from overwriting one another. This is startup
seeding, not direct store mutation. A StrategyArtifact's optional
`binding_intent` records a non-authoritative target only; approval,
`DeploymentPlan`, and `RuntimeBinding` replacement remain outside the registry
facade.

---

## 9. Open Items Held for Later Lock

This contract is now aligned to the canonical architecture, but several follow-on objects still need to land:

- `ApprovalDecision` schema and write authority from `GOV-001`
- `DeploymentPlan` contract and stage planner from `DEP-001`
- migration of `REG-002` / `REG-003` / `EX-001` metadata from `promotion_state` to `artifact_state + deployment_stage`
- experiment-backend mirroring updates in `LP-003`
- any additional canary/frozen runtime requirements once runtime-manager semantics are locked

That is why this task is still contract-first rather than a full implementation migration.

---

## 10. Review Focus

Claude should review this contract for:

- whether `artifact_state` and `deployment_stage` are now unambiguously separated
- whether derived deployment summaries are clearly marked non-authoritative
- whether the compatibility window is explicit enough for downstream migration work
