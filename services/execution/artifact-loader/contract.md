# Artifact Loader Contract

**Task:** EX-001 / EX-002-RB
**Owner:** Codex
**Reviewer:** Claude
**Status:** EX-002-RB reviewed and approved — loader now reads canonical `artifact_state + deployment_stage` (legacy `promotion_state` fallback retired by REGISTRY-LEGACY-METADATA-RETIRE-20261111); algorithm-level LEAN run coverage is still deferred

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
- `promotion_state` is no longer read by the loader (REGISTRY-LEGACY-METADATA-RETIRE-20261111). Metadata
  without `artifact_state=approved` plus canonical `deployment_stage` is rejected; a stray `promotion_state`
  field is ignored. Experiments adapter handoff/promoted metadata no longer emit it.
- Retirement evidence: read-only runtime-manager GET returned 159 bindings / 158 metadata projections, all
  with canonical `deployment_stage`, zero legacy-only. Object-store inventory via the existing MinIO client
  listed 1 bucket / 0 rows, and the local walk found 0 metadata files; neither is a claim of complete
  archive absence. See `docs/deployment/evidence/REGISTRY-LEGACY-METADATA-RETIRE-20261111/evidence.json`.
- Event `target.promotion_state` fields (EP4/EP5 scripts, TJ-E2E seed, `PaperRuntime` telemetry, training-session
  trace export) are a distinct supported business field and are unchanged.

Before artifact body load proceeds, the loader must validate:

1. metadata validates against the promoted-artifact metadata schema
2. `artifact_state=approved` when canonical split metadata is present
3. `deployment_stage` is allowed for the current execution mode
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
