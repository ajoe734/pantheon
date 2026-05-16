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
| `parent_registry_ids` | no | direct parents if this entry derives from earlier versions |
| `source_run_ids` | no | training / optimization / replication runs |
| `source_dataset_refs` | no | dataset or feature store references |
| `source_strategy_spec_id` | no | originating StrategySpec when applicable |

If an artifact reaches `approved`, lineage must not be empty.

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
| `advance_artifact_state(registry_id, target_state)` | transition an entry through governed artifact-state checks |
| `resolve_latest_approved(strategy_id)` | return the newest approved entry for a strategy |
| `resolve_deployment_view(strategy_id)` | return the derived deployment-stage view from deployment/runtime objects |

`resolve_deployment_view()` is a composed read path, not a registry-only write authority.

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
- the same `artifact_state` / `deployment_stage` split as the generic registry entry API

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
