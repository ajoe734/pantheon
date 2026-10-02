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

Investigation of container logs proved:
1. OpenClaw gateway connection errors (`127.0.0.1:18789`) and transient HTTP 503 were normal startup polling checks during model pool reconfiguration; both `openclaw-gateway` and `openclaw-gateway-adapter` were healthy before post-up readiness verification.
2. The older historical snapshot failure was disproven; `source-ingest` returned HTTP 200 OK across all snapshot requests.
3. The true root cause is that `paper-signal-producer` discovered 9 legacy active paper bindings in the hosted dev database (dating from 2026-09-13). All 9 bindings failed artifact loading because their Object Store metadata contains `"lineage": {"source_dataset_refs": null}`, violating JSON schema `services/registry/lineage/promoted_artifact_metadata.schema.json` which requires `source_dataset_refs` to be an array.
4. Because all bindings degraded, `paper-signal-producer` marked its health status `degraded`, causing the Docker healthcheck to return exit code 1 (`unhealthy`).

Per Acceptance Criterion 2:
> *"If the cause is genuinely hosted configuration only then preserve a specific evidence-backed coordinator action and remain blocked until real resolution; do not invent a source patch."*

We preserve strict readiness gates, add failure and recovery regression tests in `scripts/test_deploy_nonprod_vm.py`, and document the exact coordinator remediation required on the dev VM.

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

---

## 4. Root Cause & Isolated Reproduction

### 4.1 Schema Contract
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

### 4.2 Health Evaluation Chain
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
6. `deploy_nonprod_vm.sh:2964`:
   `verify_exact_component_deployment` detects unhealthy container and triggers compensation restore.

### 4.3 Reproduction
Reproduced locally using isolated python script:
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

In `scripts/test_deploy_nonprod_vm.py`:
1. `test_verify_exact_component_deployment_paper_signal_producer_unhealthy_prevents_activation`:
   Proves that when `paper-signal-producer` reports `health=unhealthy`, `verify_exact_component_deployment` fails closed, records the failure in `backend-components-receipt.json`, and allows compensation rollback instead of activating the candidate pair.
2. `test_verify_exact_component_deployment_paper_signal_producer_healthy_admits_pair`:
   Proves that when `paper-signal-producer` is healthy, `verify_exact_component_deployment` succeeds and admits the pair.

---

## 6. Specific Coordinator Action Runbook

To unblock deployment without bypassing gates or inventing source hacks:

1. **Option A: Retire 9 Legacy Paper Bindings (Recommended)**
   The release coordinator must retire or clean up the 9 stale bindings from the dev VM database:
   - `rb-8b36da8734534f298923e6fbb5f3eea7`
   - `rb-e3eafebeda57401b8ecdac6481e09fe7`
   - `rb-40419c598d5c436f9a735b3b7c5ccdf3`
   - `rb-9572f7b0cf9745c29ddafdc03cde136e`
   - `rb-b1f9532ba83043a3a8ef39ea95711f31`
   - `rb-8794cd13cdd649febc55c9957f0b3ba4`
   - `rb-d3a8e143ed464a40a7d26511c962c622`
   - `rb-69bfc2859ff14ff88a5006c78dcc4999`
   - `rb-30092281a9294185928793fd8d31761e`
   Using the runtime-manager API (`POST /api/runtime-bindings/<binding_id>/retire`) or database cleanup. Once retired, `paper-signal-producer` will find 0 degraded bindings, publish `status: ok`, and pass healthcheck.

2. **Option B: Bootstrap Predecessor Deployment**
   Trigger deployment with `BOOTSTRAP_EMPTY_HOST=true` and valid predecessor artifacts, re-initializing the paper binding state cleanly.

---

## 7. Verification Evidence

- `scripts/test_deploy_nonprod_vm.py`: 46 passed, 2 skipped
- `scripts/test_deploy_nonprod_bootstrap_contract.py`: 65 passed
- `scripts/test_deploy_nonprod_artifact_restore.py`: 48 passed
- Total deploy tests: 159 passed, 2 skipped in 40.48s
- `tests/test_openclaw_credential_probe.py`: 6 passed
- `services/openclaw-gateway-adapter`: 570 passed, 4 skipped in 168.35s
