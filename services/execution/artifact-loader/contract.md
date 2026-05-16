# Artifact Loader Contract

**Task:** EX-001 / EX-002-RB
**Owner:** Codex
**Reviewer:** Claude
**Status:** EX-002-RB reviewed and approved — loader now reads canonical `artifact_state + deployment_stage` (with `promotion_state` fallback for legacy object store metadata); algorithm-level LEAN run coverage is still deferred

---

## 1. Purpose

The artifact loader is the execution-side gate between governed registry artifacts and LEAN runtime.

Its job is not to decide promotion. Its job is to:

- read governed metadata from a LEAN-compatible transport path
- reject artifacts whose canonical artifact/deployment state is not allowed for the current execution mode
- verify checksum and metadata shape before artifact body load proceeds
- hand approved artifact payloads into the execution runtime
- prove that a live artifact carries governed fallback metadata, without deciding how rollback should execute

This contract defines the loader-facing metadata and behavior.

Migration note (EX-002-RB):

- canonical registry lifecycle state is `artifact_state` (draft/candidate/approved/retired)
- canonical deployment placement is `deployment_stage` (none/paper/canary/live/frozen)
- the loader now requires `artifact_state=approved` whenever canonical execution metadata is present
- the loader validates `deployment_stage` as the primary mode gate field
- `promotion_state` is accepted as a backward-compat fallback only for pre-migration object store metadata
- the execution projection emitted by `PromotionGate.build_execution_projection()` now carries canonical fields
- rollback execution semantics (`replace`, `pause_then_replace`, `liquidate_then_replace`) remain owned by
  `DeploymentPlan.rollback.action_type` and the Runtime Manager; the loader validates fallback metadata only

Reference implementation paths:

- `services/execution/artifact_loader.py`
- `services/execution/test_artifact_loader.py`
- `services/execution/smoke_test_artifact_loader.py`

Machine-readable metadata schema:

- `services/execution/artifact-loader/artifact_metadata_schema.json`

Canonical lineage/rollback schema source:

- `services/registry/lineage/promoted_artifact_metadata.schema.json`

---

## 2. Transport Rule

For v1, loader transport must use a LEAN-native Object Store path.

The loader must not depend on:

- direct local file injection into the algorithm container
- direct GCS reads that bypass LEAN runtime
- ad hoc side channels that skip governance metadata

Allowed transport assumption for this contract:

- registry or promotion tooling materializes metadata and artifact payloads into Object Store
- LEAN reads them through `ObjectStore` semantics
- Python and wrapped .NET Object Store naming differences (`object_store.read_bytes` vs `ObjectStore.ReadBytes`) are normalized by the EX-001 adapter helper

This matches QuantConnect's documented Object Store workflow for sharing data between research,
backtest, and live contexts.

---

## 3. Execution Modes

The loader must behave differently by mode:

| Mode | Required artifact state | Allowed deployment stage |
|---|---|
| `paper` | `approved` | `paper` |
| `live` | `approved` | `live` |

Rejected artifact states in both modes:

- `draft`
- `candidate`
- `retired`

Rejected deployment stages in both modes unless a later task explicitly adds a new execution mode:

- `none`
- `canary`
- `frozen`

This is a governance rule, not an optimization.

---

## 4. Required Metadata Checks

Before artifact body load proceeds, the loader must validate:

1. metadata validates against the promoted-artifact metadata schema
2. `artifact_state=approved` when canonical split metadata is present
3. `deployment_stage` (or legacy `promotion_state`) is allowed for the current execution mode
4. `checksum` is present
5. `strategy_id` and `version` are present
6. for `live`, rollback metadata exists
7. loader does **not** interpret rollback action semantics; it only verifies that the fallback artifact reference
   needed by Runtime Manager exists in metadata

If any check fails, artifact load must stop before execution uses the payload.

### 4.1 Rollback Responsibility Split

For `live` artifacts, the loader is responsible for ensuring that the execution envelope contains a governed
fallback target (`metadata.rollback.target_registry_id`, `target_version`).

The loader is **not** responsible for:

- deciding whether the runtime should `replace`, `pause_then_replace`, or `liquidate_then_replace`
- mutating `RuntimeBinding`
- timing the telemetry cutover

Those decisions belong to `DeploymentPlan.rollback.action_type` and the Runtime Manager contract.

---

## 5. Object Store Projection

The first expected Object Store keys are:

- `openclaw/registry/{strategy_id}/{version}/metadata.json`
- `openclaw/registry/{strategy_id}/{version}/artifact.bin`

The loader may later accept additional indirection or aliasing, but the first contract path
should assume those canonical keys.

---

## 6. Deferred Implementation Work

This task is still contract-first.

Still deferred after the current implementation:

- algorithm-level smoke coverage inside a real LEAN run
- artifact body deserialization into strategy-specific runtime objects beyond raw bytes

Those belong to later implementation work after the contract path is locked.

---

## 7. Review Focus

Review should confirm:

- paper/live rejection rules are explicit
- the metadata schema path is canonical and not duplicated
- transport assumptions remain LEAN-native and governance-safe
- loader rollback validation is limited to fallback metadata and does not invent parallel runtime semantics
