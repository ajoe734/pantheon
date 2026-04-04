# Strategy and Model Registry Contract

**Task:** REG-001  
**Owner:** Codex  
**Reviewer:** Gemini  
**Status:** DRAFT — skeleton ready for registry and promotion review

---

## 1. Purpose

The registry is the governed source of truth for every artifact that may eventually influence execution.

It exists so that:

- strategy evolution is versioned
- lineage is traceable
- promotion state is explicit
- rollback is possible
- LEAN execution never loads an unapproved artifact

This contract defines the registry object model before implementation details or storage backend choices are finalized.

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

Not every artifact is executable, but every artifact uses the same lifecycle vocabulary.

---

## 3. Lifecycle States

Minimum states:

| State | Meaning |
|---|---|
| `draft` | created but not yet replication-ready |
| `candidate` | passed initial normalization or replication gate |
| `paper` | approved for paper execution |
| `live` | approved for live execution |
| `retired` | no longer valid for promotion or loading |

### Allowed transitions

```text
draft -> candidate
candidate -> paper
paper -> live
live -> retired
paper -> retired
candidate -> retired
```

Rollback is not a new state. It is a transition to a previously approved entry referenced by `rollback_target`.

---

## 4. Registry Entry Model

Each registry entry must contain these fields:

| Field | Required | Description |
|---|---|---|
| `registry_id` | yes | stable unique id for the entry |
| `artifact_type` | yes | one of the artifact types in §2 |
| `strategy_id` | yes | stable strategy family identifier |
| `version` | yes | semantic version for the artifact entry |
| `lifecycle_state` | yes | `draft`, `candidate`, `paper`, `live`, `retired` |
| `lineage` | yes | source runs, parent entries, or upstream artifacts |
| `storage_ref` | yes | where the artifact bytes or payload live |
| `checksum` | yes | integrity check for the artifact payload |
| `producer_run_id` | no | training, optimization, or ingest run id |
| `evaluation_summary` | no | evaluator outputs and scores |
| `approver` | no | who approved promotion |
| `promoted_at` | no | when current lifecycle state was granted |
| `rollback_target` | no | prior approved version safe to revert to |
| `metadata` | no | non-governing supplemental fields |

---

## 5. Lineage Requirements

`lineage` is required because the registry is not just storage. It is audit and causality.

Minimum lineage subfields:

| Field | Required | Description |
|---|---|---|
| `parent_registry_ids` | no | direct parents if this entry derives from earlier versions |
| `source_run_ids` | no | training / optimization / replication runs |
| `source_dataset_refs` | no | dataset or feature store references |
| `source_strategy_spec_id` | no | originating StrategySpec when applicable |

If an artifact reaches `paper` or `live`, lineage must not be empty.

---

## 6. Execution Projection

`EX-001` requires a lean execution-facing metadata document in Object Store.

That `metadata.json` is a projection of registry state, not a separate source of truth.

Minimum execution projection fields:

| Registry field | Execution projection field |
|---|---|
| `strategy_id` | `strategy_id` |
| `version` | `version` |
| `lifecycle_state` | `promotion_state` |
| `checksum` | `checksum` |
| `promoted_at` | `approved_at` |
| `lineage` | `lineage` |

This keeps registry governance and EX-001 artifact loading aligned.

`REG-003` tightens this projection with a canonical promoted-artifact metadata schema:

- `services/registry/lineage/promoted_artifact_metadata.schema.json`
- compatibility alias: `artifact_metadata_schema.json`

### v1 Object Store projection requirements

To stay compatible with the current `EX-001` draft, the registry must be able to
materialize the following keys:

- `openclaw/registry/{strategy_id}/{version}/metadata.json`
- `openclaw/registry/{strategy_id}/{version}/artifact.bin`

And the projected `metadata.json` must satisfy these minimum rules:

| Field | Requirement |
|---|---|
| `strategy_id` | required string |
| `version` | required semantic version string (`x.y.z`) |
| `promotion_state` | required enum: `candidate`, `paper`, `live`, `retired` |
| `checksum` | required sha256 or equivalent strong checksum |
| `approved_at` | optional RFC3339 timestamp |
| `lineage` | optional object, but should be present for `paper` and `live` entries |

### Loader-facing rejection semantics

The registry projection must make these loader checks possible without extra registry calls:

- `paper` mode may only load artifacts with `promotion_state=paper`
- `live` mode may only load artifacts with `promotion_state=live`
- `candidate` and `retired` must be rejected before artifact body load proceeds

This is important because LEAN execution should validate governance state from the
projected metadata and should not need to know the registry backend.

---

## 7. Minimal Operations

The storage backend is still open, but the logical operations are not.

| Operation | Description |
|---|---|
| `register(entry)` | create a new `draft` or `candidate` entry |
| `get(registry_id)` | read one entry |
| `list_by_strategy(strategy_id)` | enumerate versions within a strategy family |
| `promote(registry_id, target_state)` | transition an entry through governed lifecycle checks |
| `resolve_live(strategy_id)` | return the currently live entry for a strategy |
| `resolve_paper(strategy_id)` | return the currently paper-approved entry |

---

## 8. Open Items Held for Later Lock

This skeleton is intentionally ahead of `OC-003`.
Before final lock, REG-001 must absorb:

- StrategySpec handoff fields from `OC-003`
- evaluator output shape from `EV-001`
- promotion gate enforcement details from `REG-002`
- finalized promoted-artifact lineage and rollback requirements from `REG-003`
- any finalized Object Store metadata requirements from `EX-001`

That is why this task is a contract skeleton first, not the final lifecycle implementation.

---

## 9. Review Focus

Gemini should review this contract for:

- compatibility with EX-001 artifact metadata projection
- whether the lifecycle states are sufficient for artifact loader checks
- whether storage and checksum fields are specific enough for Object Store based loading
