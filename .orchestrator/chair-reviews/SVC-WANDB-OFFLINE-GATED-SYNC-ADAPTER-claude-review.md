# Review: SVC-WANDB-OFFLINE-GATED-SYNC-ADAPTER

Reviewer: Claude
Date: 2026-04-30
Status: **APPROVED**

## Scope

Implement W&B offline and gated sync adapter: default offline/local run store with no SDK
import or network path; online sync explicitly gated; run/artifact refs surfaced in
promoted metadata and evaluator audit fields.

## Artifacts Reviewed

- `services/registry/experiments/adapter.py` — `LocalWandbRunStore`, `OfflineWandbLocalBackend`, `RegistryExperimentAdapter`
- `services/registry/experiments/config.py` — backend selector and gate guards
- `services/registry/experiments/test_adapter.py` — W&B-specific unit tests
- `services/registry/experiments/smoke_test.py` — `--backend wandb` smoke path
- `services/registry/experiments/WANDB_ACTIVATION.md` — gate doc (updated §3.1)
- `services/evaluation/evaluator.py` — experiment_refs audit field
- `services/evaluation/tests/test_evaluator.py` — `test_evaluate_artifact_preserves_experiment_refs_for_audit_lookup`
- `services/research-worker-gateway/tests/test_research_worker_gateway_rejection_policy.py` — wandb worker fail-closed checks
- `services/control-plane/bff/test_research_oss_preactivation_contract.py` — wandb in EXPECTED_BACKENDS

## Acceptance Criteria Evaluation

### ✓ 1. default backend remains non-networked and fail-closed

`config.py` defaults to `mlflow`. Selecting `wandb` requires
`PANTHEON_ENABLE_WANDB_OFFLINE_STORE=1` (or legacy `PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1`);
absent these, `selected_backend()` raises `EnvironmentError`. Module-level call at line 74
enforces fail-closed at import time. Online sync combined with `EXPERIMENT_BACKEND=wandb`
also raises immediately.

### ✓ 2. WandB SDK import happens only in explicit gated adapter path

`OfflineWandbLocalBackend` is pure Python + stdlib JSON — zero `import wandb` anywhere in
the offline path. The `sync_online()` method raises `ExperimentSyncError` before any SDK
import (first gate: env var check; second gate: "SDK-backed/network sync is not
implemented" sentinel). No offline code path can reach the W&B SDK.

### ✓ 3. offline local run store records metrics, params, artifact refs, and checksums

`LocalWandbRunStore.write_run()` persists:
- `record.metrics` and `record.params` in the JSON run payload
- per-artifact checksum via `_sha256(payload)` (stable JSON serialization → sha256 hex)
- `artifact_ref` URIs under `wandb-local://artifacts/<run_id>/<artifact_name>`
- per-artifact size in bytes via `stat().st_size`
- run-level checksum `_sha256(run_payload)` added before final write

`ExperimentRef.artifact_refs` carries the per-artifact ref dict back to callers.

### ✓ 4. online sync requires separate WANDB online gate and safe error policy

`sync_online()` is doubly fail-closed:
1. checks `PANTHEON_WANDB_ONLINE_SYNC_ENABLED` env var; raises if not set
2. even if the gate IS set, raises "SDK-backed/network sync is not implemented"

Test `test_wandb_online_sync_requires_explicit_gate_and_still_has_no_sdk_path` exercises
both branches explicitly.

### ✓ 5. BFF and registry can read run refs without activating online sync

`ExperimentRef.to_metadata_ref()` emits `run_id`, `run_uri`, `artifact_uri`,
`artifact_refs`, and `sync_status="offline_local"` — all from local JSON store, no network.
`evaluate_artifact()` surfaces `experiment_refs` in `auditable_fields` when present in the
artifact payload; test coverage exists.
BFF test confirms `wandb` appears in capabilities surface as `deferred/fail_closed/
capability_metadata_read_only`.

## Verification Commands Run

```
python3 -m pytest services/registry/experiments/test_adapter.py -q          → 14 passed
python3 -m pytest services/evaluation/tests/test_evaluator.py -q             → 28 passed
python3 -m pytest services/research-worker-gateway/tests/test_research_worker_gateway_rejection_policy.py -q → 7 passed
python3 -m pytest services/control-plane/bff/test_research_oss_preactivation_contract.py -q → 2 passed
python3 services/registry/experiments/smoke_test.py --backend wandb          → passed
```

## Notes

- `OfflineWandbPrepBackend = OfflineWandbLocalBackend` compatibility alias is tested and correct.
- `WANDB_ACTIVATION.md` §3.1 accurately reflects the delivered state (offline-only selector,
  no SDK pin, online gate deferred).
- `DEFERRED_OSS_ACTIVATION_MAP.md` W&B row is consistent: "Offline local-store only".
- No canonical architecture docs are mutated beyond what the task scope requires.

## Decision

**APPROVED** — all five acceptance criteria met, all verification commands pass.
Returned to Codex2 (owner) for closeout finalization.
