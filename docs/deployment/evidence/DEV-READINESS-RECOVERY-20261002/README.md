# Nonprod Readiness Failure Diagnosis & Recovery Evidence

**Task ID**: `DEV-READINESS-RECOVERY-20261002`  
**Owner**: `Antigravity2`  
**Reviewer**: `Codex2`  
**Target Environment**: Dev VM (`pantheon-dev-environment`)  
**Inspected CI Run**: GitHub Actions run `37006617043` (`dev Pantheon Nonprod Deploy`)  

---

## 1. Executive Summary

Actions run `37006617043` failed during the step `Deploy dev VM stack under lease` at `verify_exact_component_deployment` (line 3920 of `scripts/deploy_nonprod_vm.sh`). The deployment aborted safely and fail-closed:
```text
[remote-deploy] required component(s) unhealthy or unknown: paper-signal-producer: health=unhealthy
[remote-deploy] post-up failure at exact_component_deployment; attempting sealed exact-artifact BFF restore
[remote-deploy] automatic BFF exact-artifact restore verified
Process completed with exit code 75.
```
The exact-artifact compensation successfully restored the prior accepted baseline (`backend: f3e267c1fd700e9452470e43d73da3c3dd57146e`, `frontend: d2d3a0c7b0a1bf0943e01e129174e235f39d8039`) without activating the candidate pair.

Investigation of container logs and control-plane projection proved:
1. OpenClaw gateway connection errors (`127.0.0.1:18789`) and transient HTTP 503 were normal startup polling checks during model pool reconfiguration; both `openclaw-gateway` and `openclaw-gateway-adapter` were healthy before post-up readiness verification.
2. The older historical snapshot failure was disproven; `source-ingest` returned HTTP 200 OK across all snapshot requests.
3. The root cause is a source defect in `services/control-plane/governance/deployment_plan.py` where `_normalize_lineage` copied Registry null Lineage fields directly into promoted artifact metadata. The schema `services/registry/lineage/promoted_artifact_metadata.schema.json` types `source_dataset_refs` as an array, so `paper-signal-producer` rejected every binding that loaded such metadata.
4. Read-only VM evidence confirms that runtime binding `rb-30092281` (`persona-ae7b97a96e92acd86682`) was created 2026-10-02 02:35 by dev `67118cb23` and also has Object Store metadata `lineage.source_dataset_refs=null`. This disproved the hypothesis that the issue was hosted-only legacy data: retiring existing bindings alone would cause the next baseline binding to fail again.
5. The source defect is fixed in commit `ef3e8f69db376afe93a2b8d68940a0efae0cdd35` on PR #6097 by omitting null Lineage fields as `Lineage.to_dict()` does, verified with regression `test_projection_omits_null_registry_lineage_fields`.
6. Existing bindings already persisted in the dev VM database still carry null fields; the coordinator runbook details their retirement on the dev VM by the Human/Ops coordinator.

---

## 2. CI Run 37006617043 Analysis

### 2.1 Timeline and Failure Point
- **Step**: `Deploy dev VM stack under lease`
- **Caller**: `verify_exact_component_deployment` (line 3920 of `scripts/deploy_nonprod_vm.sh`)
- **Docker Health State**:
  ```text
  pantheon-paper-signal-producer-1 | Up 4 minutes (unhealthy)
  status=running restart_count=0 health=unhealthy exit_code=0 oom_killed=false error=""
  ```

### 2.2 Container Service Logs
```text
paper-signal-producer-1  | INFO:__main__:Discovered 9 active paper bindings from runtime-manager
paper-signal-producer-1  | WARNING:__main__:paper_signal_producer degraded binding rb-8b36da8734534f298923e6fbb5f3eea7: artifact_unavailable: binding rb-8b36da8734534f298923e6fbb5f3eea7 cannot load its approved artifact: Metadata schema validation failed at lineage.source_dataset_refs: None is not of type 'array'
paper-signal-producer-1  | WARNING:__main__:paper_signal_producer degraded binding rb-e3eafebeda57401b8ecdac6481e09fe7: ...
paper-signal-producer-1  | WARNING:__main__:paper_signal_producer degraded binding rb-40419c598d5c436f9a735b3b7c5ccdf3: ...
paper-signal-producer-1  | WARNING:__main__:paper_signal_producer degraded binding rb-9572f7b0cf9745c29ddafdc03cde136e: ...
paper-signal-producer-1  | WARNING:__main__:paper_signal_producer degraded binding rb-b1f9532ba83043a3a8ef39ea95711f31: ...
paper-signal-producer-1  | WARNING:__main__:paper_signal_producer degraded binding rb-8794cd13cdd649febc55c9957f0b3ba4: ...
paper-signal-producer-1  | WARNING:__main__:paper_signal_producer degraded binding rb-d3a8e143ed464a40a7d26511c962c622: ...
paper-signal-producer-1  | WARNING:__main__:paper_signal_producer degraded binding rb-69bfc2859ff14ff88a5006c78dcc4999: ...
paper-signal-producer-1  | WARNING:__main__:paper_signal_producer degraded binding rb-30092281a9294185928793fd8d31761e: ...
```

---

## 3. Disproven Surface Observations

1. **OpenClaw Gateway `:18789` & HTTP 503**:
   - `curl: (7) Failed to connect to 127.0.0.1 port 18789` occurred during the execution of `scripts/openclaw-configure-shared-model-pool.sh` when the gateway container was restarting to apply configuration.
   - At the time `verify_exact_component_deployment` ran, `docker compose ps` showed:
     - `pantheon-openclaw-gateway-1`: `Up About a minute (healthy)`
     - `pantheon-openclaw-gateway-adapter-1`: `Up 5 minutes (healthy)`
   - Therefore, OpenClaw was not the cause of deployment failure.

2. **Older Snapshot Failure**:
   - In earlier run `36954831846`, a snapshot query had failed.
   - In run `37006617043`, `source-ingest` returned `200 OK` on `/api/source-ingest/snapshots/latest?symbol=SPY` and was fully healthy.

3. **Hosted-Only Data Defect Hypothesis**:
   - Initially suspected to be only legacy database records from 2026-09-13.
   - Disproven by VM evidence: binding `rb-30092281` (`persona-ae7b97a96e92acd86682`) created 2026-10-02 02:35 by dev commit `67118cb23` also carries `lineage.source_dataset_refs=null`. The control-plane projection in `deployment_plan.py` actively generated null lineage fields on new bindings.

---

## 4. Root Cause, Source Fix & Isolated Reproduction

### 4.1 Root Cause & Schema Contract
In `services/registry/lineage/promoted_artifact_metadata.schema.json`:
```json
"source_dataset_refs": {
  "type": "array",
  "items": {
    "type": "string"
  }
}
```
A value of `null` (`None` in Python) violates this schema.

The Registry API serializes absent `Lineage` fields as `null`. When `deployment_plan.py`'s `_normalize_lineage` ran:
```python
# Before fix:
normalized = dict(lineage)
```
it copied those `null` entries into the promoted artifact metadata projection.

### 4.2 Source Fix
In `services/control-plane/governance/deployment_plan.py`:
```python
# After fix (commit ef3e8f69db376afe93a2b8d68940a0efae0cdd35):
normalized = {key: value for key, value in lineage.items() if value is not None}
```
Null fields are omitted, consistent with `Lineage.to_dict()` behavior.

### 4.3 Health Evaluation Chain
1. `services/execution/artifact_loader.py:379`:
   `Draft7Validator.iter_errors(metadata)` raises:
   `ArtifactLoadError("Metadata schema validation failed at lineage.source_dataset_refs: None is not of type 'array'")`
2. `services/execution/lean_runtime/paper_signal_producer.py:308`:
   Catches `ArtifactLoadError`, marks binding as degraded.
3. `services/execution/lean_runtime/paper_signal_producer.py:1126`:
   Sets `health["status"] = "degraded"` in `/tmp/paper-signal-producer-health.json`.
4. `services/worker_health.py:81`:
   `check_worker_health` fails with exit code 1 if `status != "ok"`.
5. Docker healthcheck marks container `unhealthy`.
6. `deploy_nonprod_vm.sh:3920`:
   `verify_exact_component_deployment` detects unhealthy container and triggers compensation restore.

### 4.4 Isolated Reproduction
```python
import json, jsonschema
from pathlib import Path
schema = json.loads(Path("services/registry/lineage/promoted_artifact_metadata.schema.json").read_text())
validator = jsonschema.Draft7Validator(schema)
errors = list(validator.iter_errors({"lineage": {"source_dataset_refs": None}}))
# Output: None is not of type 'array' at lineage.source_dataset_refs
```

---

## 5. Regressions Added

1. In `services/control-plane/governance/test_deployment_plan.py`:
   - `test_projection_omits_null_registry_lineage_fields`:
     Validates that `build_execution_projection` omits null Lineage fields (`source_dataset_refs: None`, `parent_registry_ids: None`, `source_strategy_spec_id: None`) from the registry entry, producing lineage metadata that passes `promoted_artifact_metadata.schema.json` validation.

2. In `scripts/test_deploy_nonprod_vm.py`:
   - `test_verify_exact_component_deployment_paper_signal_producer_unhealthy_prevents_activation`:
     Proves that when `paper-signal-producer` reports `health=unhealthy`, `verify_exact_component_deployment` fails closed, records the failure in `backend-components-receipt.json`, and allows compensation rollback instead of activating the candidate pair.
   - `test_verify_exact_component_deployment_paper_signal_producer_healthy_admits_pair`:
     Proves that when `paper-signal-producer` is healthy, `verify_exact_component_deployment` succeeds and admits the pair.
   - `test_paper_signal_producer_binding_recovery_and_health_lifecycle`:
     Substantiates and proves the degraded caching behavior and supported recovery procedures by driving the bounded production loop (`main()`, `write_health()`, and `healthcheck()`) with controlled binding and strategy dependencies.

---

## 6. Coordinator Runbook for Existing Null-Lineage Bindings

Existing bindings persisted in the dev VM database prior to this fix still carry `source_dataset_refs: null` and must be retired by the release coordinator:

### 6.1 Supported Recovery Procedures
1. **Procedure A: Retire 9 Legacy Paper Bindings & Restart Container (Recommended)**
   The release coordinator retires the 9 stale bindings on the dev VM database:
   - `rb-8b36da8734534f298923e6fbb5f3eea7`
   - `rb-e3eafebeda57401b8ecdac6481e09fe7`
   - `rb-40419c598d5c436f9a735b3b7c5ccdf3`
   - `rb-9572f7b0cf9745c29ddafdc03cde136e`
   - `rb-b1f9532ba83043a3a8ef39ea95711f31`
   - `rb-8794cd13cdd649febc55c9957f0b3ba4`
   - `rb-d3a8e143ed464a40a7d26511c962c622`
   - `rb-69bfc2859ff14ff88a5006c78dcc4999`
   - `rb-30092281a9294185928793fd8d31761e`
   Using the runtime-manager API (`POST /api/runtime-bindings/<binding_id>/retire`) or database cleanup.
   Then restart `paper-signal-producer` (`docker compose restart paper-signal-producer` or stack deployment container recreate).
   On restart, the producer discovers 0 active bindings, publishes `status: ok`, and passes the Docker healthcheck.

2. **Procedure B: Retire Legacy Bindings & Activate Valid Binding**
   Retire the 9 stale bindings and activate at least one valid paper binding with valid metadata. On tick, `producer.tick()` purges the stale degraded bindings from memory and restores `status: ok` without a container restart.

### 6.2 Execution Boundary
- Workers have no dev VM or SSH access, no Compose grant, no deployment trigger, and no database cleanup authority.
- The source defect is fixed and covered by regression tests on task branch PR #6097.
- Hosted cleanup of existing bindings in the dev VM database and the redeploy are performed by the Human/Ops coordinator.

---

## 7. Verification Evidence

- `services/control-plane/governance/test_deployment_plan.py`: 34 passed in 4.51s
- `scripts/test_deploy_nonprod_vm.py` (paper_signal_producer regressions): 3 passed in 4.22s
- `scripts/test_deploy_nonprod_bootstrap_contract.py`: 65 passed
- `services/control-plane/governance/test_persona_proposal_runtime_binding_e2e.py` & `test_artifact_loader.py` & `test_paper_runtime_binding.py` & `test_deployment_saga.py`: 37 passed in 16.06s
- `services/control-plane/bff/tests/test_deployment_router.py`: 4 passed in 4.28s
