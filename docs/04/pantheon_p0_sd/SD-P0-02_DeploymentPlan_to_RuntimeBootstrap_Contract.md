---
project: Pantheon
document_type: P0 System Design / Architecture Decision / Codex Implementation Packet
language: zh-TW
status: draft-for-implementation
revision: v1
baseline: >
  Based on Pantheon consolidated blueprint and latest implementation correction:
  current actual LEAN bridge is `pantheon/lean` submodule, remote `ajoe734/pantheon-lean.git`;
  `lean-platform` is not the current Pantheon execution target.
---

# SD-P0-02 — DeploymentPlan → runtime_bootstrap Contract

## 1. Purpose

本 SD 定義 Pantheon Governance / Promotion Plane 產生的 `DeploymentPlan` 如何進入目前 execution bootstrap：

```text
DeploymentPlan
→ RuntimeBinding
→ runtime_bootstrap.py
→ pantheon/lean submodule
→ PantheonAlgoBase / paper runtime
```

此文件的目標是先建立 **paper-only Minimum Operating Loop** 的 launch contract。
本文件不啟用 live broker execution。

---

## 2. Current Facts

目前狀態：

```text
execution Dockerfile:
  Python slim

entrypoint:
  services/execution/lean_runtime/runtime_bootstrap.py

runtime behavior:
  paper role starts Python paper runtime
  live role is health-only sidecar placeholder

compose:
  docker-compose.exec.yml points to /workspace/lean/Launcher/config.json

bridge:
  pantheon/lean submodule
  remote = ajoe734/pantheon-lean.git
  contains PantheonAlgoBase bridge

known gap:
  bracket order is log-only
  full Lean Launcher + broker SDK production execution kernel is not complete
```

---

## 3. Design Goal

The P0 contract must allow Pantheon to prove:

```text
approved paper deployment
→ creates RuntimeBinding
→ starts paper runtime via runtime_bootstrap
→ runtime knows enough identity to emit telemetry
→ live remains fail-closed
```

---

## 4. Target Repo / Paths

### 4.1 pantheon repo

```text
services/execution/lean_runtime/runtime_bootstrap.py
services/execution/lean_runtime/
services/registry/promotion/
services/runtime/
services/telemetry/
docker-compose.exec.yml
```

### 4.2 bridge repo

```text
pantheon/lean/
pantheon/lean/pantheon_algo/base.py
```

### 4.3 frontend repo

No direct code changes in this SD except reading status from BFF.
Frontend-specific work belongs to SD-P0-05.

---

## 5. Domain Objects

### 5.1 DeploymentPlan

Minimum P0 fields:

```json
{
  "deployment_plan_id": "dp-...",
  "artifact_id": "art-...",
  "artifact_version": "1.0.0",
  "artifact_checksum": "sha256:...",
  "strategy_id": "strat-...",
  "capital_pool_id": "pool-...",
  "target_stage": "paper",
  "runtime_role": "paper",
  "runtime_profile": "pantheon_lean_paper_v1",
  "approval_decision_id": "appr-...",
  "persona_capital_binding_id": "pcb-...",
  "created_at": "RFC3339",
  "created_by": "actor-...",
  "pre_checks": [],
  "post_checks": []
}
```

### 5.2 RuntimeBinding

Minimum P0 fields:

```json
{
  "runtime_binding_id": "rtb-...",
  "runtime_id": "rt-...",
  "deployment_plan_id": "dp-...",
  "artifact_id": "art-...",
  "artifact_version": "1.0.0",
  "capital_pool_id": "pool-...",
  "deployment_stage": "paper",
  "runtime_role": "paper",
  "engine_bridge_repo": "ajoe734/pantheon-lean.git",
  "engine_bridge_path": "pantheon/lean",
  "engine_bridge_commit": "...",
  "status": "created|loading|active|degraded|paused|terminated",
  "effective_at": "RFC3339",
  "rollback_parent": null
}
```

### 5.3 RuntimeBootstrapRequest

```json
{
  "request_id": "uuid",
  "trace_id": "uuid",
  "runtime_binding_id": "rtb-...",
  "deployment_plan_id": "dp-...",
  "runtime_role": "paper",
  "deployment_stage": "paper",
  "bridge": {
    "path": "/workspace/lean",
    "remote": "ajoe734/pantheon-lean.git",
    "commit": "..."
  },
  "artifact": {
    "artifact_id": "art-...",
    "artifact_version": "1.0.0",
    "checksum": "sha256:...",
    "strategy_id": "strat-..."
  },
  "capital": {
    "capital_pool_id": "pool-...",
    "persona_capital_binding_id": "pcb-..."
  },
  "runtime_config": {
    "config_ref": "/workspace/lean/Launcher/config.json",
    "paper_mode": true,
    "live_broker_enabled": false
  }
}
```

### 5.4 RuntimeBootstrapResult

```json
{
  "request_id": "uuid",
  "runtime_binding_id": "rtb-...",
  "runtime_id": "rt-...",
  "status": "started|failed|health_only|blocked",
  "started_at": "RFC3339",
  "blocking_reasons": [],
  "health_endpoint": "/healthz",
  "ready_endpoint": "/readyz",
  "telemetry_status": "pending|emitted|failed",
  "engine_bridge_commit": "..."
}
```

---

## 6. Commands

```text
CreateDeploymentPlan
CreateRuntimeBinding
MaterializeRuntimeBootstrapRequest
StartRuntimeBootstrap
MarkRuntimeLoading
MarkRuntimeActive
MarkRuntimeBlocked
MarkRuntimeHealthOnly
```

### 6.1 Command contract

Every runtime-affecting command must include:

```json
{
  "command_id": "uuid",
  "actor_ref": "operator|system|scheduler",
  "actor_role": "operator|admin|system",
  "idempotency_key": "...",
  "trace_id": "uuid",
  "reason": "...",
  "target_ref": {
    "type": "DeploymentPlan|RuntimeBinding",
    "id": "..."
  }
}
```

---

## 7. Events

```text
DeploymentPlanCreated
RuntimeBindingCreated
RuntimeBootstrapRequested
RuntimeBootstrapStarted
RuntimeBootstrapBlocked
RuntimeBootstrapHealthOnly
RuntimeBootstrapCompleted
RuntimeBootstrapFailed
```

### 7.1 Event envelope

```json
{
  "event_id": "uuid",
  "event_type": "RuntimeBootstrapStarted",
  "event_time": "RFC3339",
  "producer": "pantheon.execution.runtime_bootstrap",
  "trace_id": "uuid",
  "correlation_id": "uuid",
  "runtime_binding_id": "rtb-...",
  "deployment_plan_id": "dp-...",
  "payload": {}
}
```

---

## 8. Hard Invariants

```text
INV-BOOT-001:
  runtime_bootstrap MUST NOT start live broker execution by default.

INV-BOOT-002:
  target_stage=live MUST fail closed unless explicit live activation policy is enabled.

INV-BOOT-003:
  runtime_role=paper may start Python paper runtime without broker SDK.

INV-BOOT-004:
  runtime_role=live currently starts health-only sidecar unless production activation flag is approved.

INV-BOOT-005:
  RuntimeBootstrapRequest MUST reference RuntimeBinding.

INV-BOOT-006:
  RuntimeBootstrapRequest MUST reference DeploymentPlan.

INV-BOOT-007:
  RuntimeBootstrapRequest MUST include bridge path and bridge commit.

INV-BOOT-008:
  DeploymentPlan target must point to `pantheon/lean`, not `lean-platform`, for current P0.

INV-BOOT-009:
  Broker secret MUST NOT be included in RuntimeBootstrapRequest.

INV-BOOT-010:
  bracket order behavior MUST remain logged_only until guarded broker execution is implemented.
```

---

## 9. Policy-configurable Rules

```text
1. Whether `paper` can run in dev, staging, prod.
2. Whether `canary` role is allowed.
3. Whether `live` role activation flag is allowed.
4. Required pre-checks for paper runtime.
5. Required telemetry events before RuntimeBinding becomes active.
6. Required retry count for runtime bootstrap.
```

---

## 10. Runtime Role Behavior

### 10.1 paper

```text
Allowed:
  - start Python paper runtime
  - simulate orders
  - emit heartbeat
  - emit paper telemetry
  - produce runtime health

Forbidden:
  - live broker order
  - live credential access
  - canary/live capital pool binding
```

### 10.2 canary

```text
Current P0:
  blocked / not activated

Required future:
  explicit canary activation policy
  canary broker entitlement
  reduced budget
  rollback parent
```

### 10.3 live

```text
Current P0:
  health-only sidecar
  fail-closed for any broker action

Allowed:
  - /healthz
  - /readyz if health-only ready
  - metrics showing not activated

Forbidden:
  - broker connect
  - order placement
  - bracket order submission
  - capital pool live mutation
```

---

## 11. Failure Behavior

| Failure | Behavior |
|---|---|
| Missing RuntimeBinding | fail closed |
| Missing DeploymentPlan | fail closed |
| target repo is lean-platform | fail closed unless migration_override |
| live role without activation | health-only sidecar / fail closed |
| broker secret in request | reject and audit |
| artifact checksum missing | fail closed for non-dev |
| telemetry heartbeat failure | runtime starts degraded, not active |
| bridge commit unknown | degraded / unverifiable |

---

## 12. Tests

### 12.1 Unit tests

```text
test_materialize_bootstrap_request_from_deployment_plan
test_bootstrap_request_requires_runtime_binding_id
test_bootstrap_request_requires_deployment_plan_id
test_bootstrap_request_rejects_lean_platform_target
test_live_role_defaults_to_health_only
test_paper_role_does_not_require_broker_secret
```

### 12.2 Integration tests

```text
test_runtime_bootstrap_paper_role_starts
test_runtime_bootstrap_live_role_health_only
test_runtime_bootstrap_emits_started_event
test_runtime_bootstrap_blocks_missing_binding
test_runtime_bootstrap_reports_bridge_commit
```

### 12.3 E2E paper baseline test

```text
DeploymentPlan(target_stage=paper)
→ RuntimeBinding
→ RuntimeBootstrapRequest
→ runtime_bootstrap.py
→ paper runtime starts
→ heartbeat emitted
→ BFF runtime summary visible
```

---

## 13. Non-goals

```text
1. Do not implement full live broker SDK in this SD.
2. Do not activate canary or live.
3. Do not migrate to lean-platform.
4. Do not remove dev JSON/JSONL fallback.
5. Do not enable OpenClaw broker kernel.
6. Do not implement bracket order broker submission.
7. Do not implement BFF HA/LB.
```

---

## 14. Acceptance Criteria

```text
AC-BOOT-001:
  A paper DeploymentPlan can produce a RuntimeBootstrapRequest.

AC-BOOT-002:
  runtime_bootstrap paper role starts and reports health.

AC-BOOT-003:
  live role is health-only and cannot place broker orders.

AC-BOOT-004:
  request references pantheon/lean bridge identity.

AC-BOOT-005:
  no P0 execution path targets lean-platform.

AC-BOOT-006:
  bootstrap result can be correlated to RuntimeBinding.

AC-BOOT-007:
  tests prove bracket order remains logged_only until activation.
```

---

## 15. Codex Task Packets

### TP-BOOT-001 — Implement RuntimeBootstrapRequest materializer

```yaml
task_id: TP-BOOT-001
repo: pantheon
goal: Materialize RuntimeBootstrapRequest from DeploymentPlan and RuntimeBinding.
target_paths:
  - services/execution/lean_runtime/bootstrap_contract.py
  - services/execution/lean_runtime/tests/test_bootstrap_contract.py
acceptance:
  - request contains runtime_binding_id
  - request contains deployment_plan_id
  - request contains bridge identity
  - rejects lean-platform target
non_goals:
  - do not enable live broker
```

### TP-BOOT-002 — Add live health-only fail-closed test

```yaml
task_id: TP-BOOT-002
repo: pantheon
goal: Verify runtime_bootstrap live role is health-only by default.
target_paths:
  - services/execution/lean_runtime/runtime_bootstrap.py
  - services/execution/lean_runtime/tests/test_runtime_bootstrap.py
acceptance:
  - live role does not connect broker
  - live role exposes health
  - live role reports not_activated
```

### TP-BOOT-003 — Add paper runtime smoke test

```yaml
task_id: TP-BOOT-003
repo: pantheon
goal: Add smoke test for paper runtime bootstrap.
target_paths:
  - services/execution/lean_runtime/tests/test_paper_runtime_smoke.py
acceptance:
  - paper role starts
  - returns runtime_id
  - emits or schedules heartbeat
```

### TP-BOOT-004 — Update compose metadata

```yaml
task_id: TP-BOOT-004
repo: pantheon
goal: Ensure docker-compose.exec.yml exposes bridge path and commit metadata.
target_paths:
  - docker-compose.exec.yml
acceptance:
  - /workspace/lean path preserved
  - bridge repo metadata available to runtime
```

---

## 16. P0-BOOT-001 Implementation Record

Implemented by `P0-BOOT-001`:

```text
services/execution/lean_runtime/bootstrap_contract.py
services/execution/lean_runtime/test_bootstrap_contract.py
```

Materializer behavior:

```text
DeploymentPlan + RuntimeBinding
→ RuntimeBootstrapRequest
```

The request includes:

```text
runtime_binding_id
deployment_plan_id
runtime_id
deployment_stage
runtime_role
artifact_id / artifact_version / checksum / strategy_id
capital_pool_id / persona_capital_binding_id
bridge.path / bridge.source_path / bridge.remote / bridge.commit
runtime_config.config_ref / paper_mode / live_broker_enabled / health_only
```

Safety gates:

```text
raw secret-bearing keys are rejected
lean-platform targets are rejected
bridge remote must be ajoe734/pantheon-lean.git
bridge source path must be pantheon/lean
live_broker_enabled=true is rejected in P0
live/canary requests default to health_only with broker disabled
```

Verification:

```bash
python3 -m pytest services/execution/lean_runtime/test_bootstrap_contract.py services/execution/lean_runtime/test_runtime_identity.py services/execution/lean_runtime/test_paper_runtime.py
```
