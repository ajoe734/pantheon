# Promoted Artifact Lineage Contract

**Task:** REG-003  
**Owner:** Codex  
**Reviewer:** Claude  
**Status:** DRAFT — first rollback/lineage contract ready for governance review

---

## 1. Purpose

`REG-003` extends `REG-001` and `REG-002` with one stricter rule:

Any artifact that reaches governed promotion states must carry enough lineage and rollback
metadata to support:

- auditability
- safe rollback
- loader-side validation
- experiment-backend linkage

This contract defines the execution-facing metadata shape for promoted artifacts.

Compatibility note after `REG-004`:

- canonical registry state is now `artifact_state`
- canonical runtime placement is now `deployment_stage`
- this document still describes the legacy execution-facing metadata envelope that current `REG-002` /
  `EX-001` code emits via `promotion_state`
- follow-on migration work will move this envelope to `artifact_state + deployment_stage` without
  making this file the source of truth for registry lifecycle semantics

Machine-readable schema:

- `services/registry/lineage/promoted_artifact_metadata.schema.json`

Compatibility alias:

- `artifact_metadata_schema.json`

The root-level schema exists only to preserve the current `EX-001` / `REG-002` file path.
The canonical schema lives under `services/registry/lineage/`.

---

## 2. Scope

This contract applies to artifacts that may be loaded or referenced after promotion, especially:

- `candidate`
- `paper`
- `live`
- `retired`

It does not redefine the full registry entry model from `REG-001`.
Instead, it defines the minimum promoted-artifact metadata envelope that downstream systems
must be able to trust.

---

## 3. Required Fields

Every promoted artifact metadata document must contain:

| Field | Required | Description |
|---|---|---|
| `registry_id` | yes | stable registry entry id |
| `strategy_id` | yes | strategy family identifier |
| `version` | yes | semantic version of the promoted artifact |
| `artifact_type` | yes | governed artifact type |
| `promotion_state` | yes | `candidate`, `paper`, `live`, or `retired` |
| `checksum` | yes | strong checksum for the artifact payload |
| `lineage` | yes | causality and source references |
| `created_at` | yes | metadata creation timestamp |
| `approved_at` | no | timestamp when promotion was granted |
| `approver` | no | actor that approved the current state |
| `rollback` | no | rollback metadata object, required for `live` |
| `experiment_refs` | no | references into MLflow or W&B once integrated |

---

## 4. Lineage Rules

`lineage` is mandatory for all promoted artifact metadata, but the minimum acceptable depth
depends on promotion state.

### Candidate

Must contain at least one of:

- `source_run_ids`
- `source_strategy_spec_id`
- `source_dataset_refs`

### Paper

Must contain:

- at least one source reference
- `parent_registry_ids` if the artifact derives from a prior promoted artifact

### Live

Must contain:

- at least one source reference
- enough history to identify the immediately prior approved artifact
- a rollback object that points to a safe prior target

---

## 5. Rollback Rules

Rollback is not inferred. It must be explicit for `live` artifacts.

### `rollback` object

| Field | Required | Description |
|---|---|---|
| `target_registry_id` | yes for `live` | previously approved entry safe to revert to |
| `target_version` | yes for `live` | prior approved semantic version |
| `reason` | no | why this target is the rollback default |
| `verified_at` | no | when rollback safety was last checked |

Rules:

1. `rollback.target_registry_id` must not equal the current `registry_id`
2. `rollback.target_version` must not equal the current `version`
3. `live` artifacts without rollback metadata are invalid
4. `paper` artifacts may include rollback hints, but are not required to

---

## 6. Loader-Facing Semantics

The metadata must allow loader-side checks without a second registry lookup.

Minimum checks supported by this contract:

1. reject `candidate` and `retired` for live loader paths
2. verify checksum before artifact body load proceeds
3. surface rollback target for operator or automated remediation tooling
4. retain enough lineage to map from an execution incident back to:
   - source run
   - source dataset
   - parent registry entry

---

## 7. Experiment Backend Compatibility

This contract anticipates `LP-003` without making the experiment backend authoritative.

`experiment_refs` may later include:

- `backend`: `mlflow` or `wandb`
- `run_id`
- `artifact_uri`
- `project`
- `aliases`

But the governed source of truth for promotion remains the local registry.

---

## 8. Review Focus

Claude should review this draft for:

- whether rollback requirements are strict enough for live governance
- whether loader-side checks can be satisfied from this envelope alone
- whether the lineage minimums are strong enough to support incident response
