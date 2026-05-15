# SD-08 — Execution Runtime Binding / pantheon-lean 執行底座與 Runtime 邊界設計

版本：v0.1 Codex-ready draft
適用範圍：Execution Plane、Runtime Manager、Artifact Loader、Runtime Binding Store、LEAN Paper / Canary / Live Runtime、Broker / Exchange / Subaccounts、Pause / Liquidate / Replace Actions
前置依賴：SD-00、SD-01、SD-06 Capital Pool Governance、SD-07 Promotion / Deployment

---

## 1. Purpose

本文件定義 Pantheon 如何透過 `pantheon-lean` 承接 execution-related substrate。

重點不是讓 Pantheon 直接呼叫 broker，而是建立明確 runtime boundary：

```text
DeploymentPlan ready
→ ExecutionRequest
→ Runtime Manager
→ Artifact Loader
→ RuntimeBinding
→ pantheon-lean paper/canary/live runtime
→ canonical telemetry events
```

`pantheon-lean` 是目前藍圖指定的 execution substrate；`Lean` 只作為 upstream reference / patch source，不作為 Pantheon 內部平行 runtime 主線。

---

## 2. Repo ownership

| Repo | Ownership |
|---|---|
| `pantheon-lean` | Primary execution substrate：runtime manager adapter、artifact loader、broker integration、order/fill/position/heartbeat event export。 |
| `pantheon` | RuntimeBinding truth、ExecutionRequest API、deployment state, telemetry ingest boundary。 |
| `front-ai-trading-system` | UI consumer：Runtime Manager UI、Runtime Detail、Operator Actions、Health / Kill Switch controls。 |
| `Lean` | Upstream mirror/reference only；no direct Pantheon production authority。 |

---

## 3. Module paths

### `pantheon`

```text
services/execution/
  __init__.py
  models.py
  commands.py
  queries.py
  events.py
  runtime_binding_store.py
  execution_gateway.py
  runtime_action_policy.py
  repository.py
  api.py
  tests/

docs/contracts/execution_request.schema.json
docs/contracts/runtime_binding.schema.json
docs/contracts/runtime_status.schema.json
docs/contracts/runtime_action.schema.json
docs/sd/08_execution_runtime_binding.md
docs/codex/SD-08_task_packets.md
```

### `pantheon-lean`

```text
Pantheon/
  RuntimeBridge/
    PantheonRuntimeBridge.cs
    PantheonDeploymentPlanReader.cs
    PantheonArtifactLoader.cs
    PantheonRuntimeBindingEmitter.cs
    PantheonTelemetryEmitter.cs
    PantheonRuntimeActionHandler.cs
    Models/
      ExecutionRequest.cs
      RuntimeBinding.cs
      RuntimeStatus.cs
      TelemetryEvent.cs
    Tests/

Engine/Setup/
  BrokerageSetupHandler.cs        # inject runtime_binding_id / capital_pool_id / artifact_id context

Brokerages/
  BaseWebsocketsBrokerage.cs       # emit connection health and broker event metadata

ToolBox/Polygon/*                 # may remain connector reference; not canonical data authority
ToolBox/Benzinga/*                # may remain connector reference; news canonicalization belongs to SD-03
```

### `front-ai-trading-system`

```text
src/pages/operator/RuntimeManager.tsx
src/pages/operator/RuntimeDetail.tsx
src/pages/operator/RuntimeActionsPanel.tsx
src/types/runtime.ts
src/lib/runtimeClient.ts
```

---

## 4. Domain model

### 4.1 `ExecutionRequest`

```yaml
ExecutionRequest:
  execution_request_id: string
  deployment_plan_id: string
  artifact_id: string
  capital_pool_id: string
  target_mode: enum[paper, canary, live]
  runtime_action: enum[create, replace, restart, rollback, pause, liquidate]
  runtime_config_ref: string
  loader_report_id: string
  requested_by: actor_ref
  status: enum[submitted, accepted, rejected, loading, active, failed, cancelled]
  trace_id: string
  created_at: datetime
```

### 4.2 `RuntimeBinding`

```yaml
RuntimeBinding:
  binding_id: string
  runtime_id: string
  execution_request_id: string
  deployment_plan_id: string
  capital_pool_id: string
  artifact_id: string
  deployment_mode: enum[paper, canary, live]
  environment: enum[paper, canary, live]
  version: string
  effective_at: datetime
  status: enum[pending, loading, active, degraded, paused, replacing, terminated, failed]
  rollback_parent: string | null
  trace_id: string
```

### 4.3 `RuntimeSpec`

```yaml
RuntimeSpec:
  runtime_spec_id: string
  runtime_group: string
  target_mode: enum[paper, canary, live]
  engine: enum[lean]
  artifact_loader_type: string
  broker_account_ref: string
  market_data_profile_ref: string | null
  resource_profile_ref: string | null
  environment_vars_ref: string | null
  status: enum[enabled, disabled]
```

### 4.4 `ArtifactLoadRequest`

```yaml
ArtifactLoadRequest:
  load_request_id: string
  runtime_id: string
  artifact_id: string
  artifact_manifest_ref: string
  deployment_plan_id: string
  loader_report_id: string
  status: enum[pending, validated, loaded, failed]
  failure_reason: string | null
```

### 4.5 `RuntimeStatus`

```yaml
RuntimeStatus:
  runtime_id: string
  binding_id: string
  state: enum[created, loading, active, degraded, paused, replacing, terminated]
  health_summary:
    heartbeat_status: enum[ok, stale, missing]
    broker_status: enum[connected, degraded, disconnected]
    data_status: enum[ok, delayed, missing]
    order_status: enum[ok, blocked, error]
  last_heartbeat: datetime | null
  pending_action: string | null
  observed_at: datetime
```

### 4.6 `RuntimeAction`

```yaml
RuntimeAction:
  action_id: string
  runtime_id: string
  binding_id: string
  action_type: enum[pause, resume, liquidate, replace, restart, terminate, safe_mode]
  requested_by: actor_ref
  reason: string
  approval_ref: string | null
  status: enum[requested, approved, executing, executed, failed, cancelled]
  trace_id: string
```

### 4.7 `ExecutionTelemetryEvent`

```yaml
ExecutionTelemetryEvent:
  event_id: string
  event_type: enum[runtime_heartbeat, order_submitted, order_updated, fill, position_snapshot, cash_snapshot, broker_connection, runtime_state, runtime_action]
  runtime_id: string
  binding_id: string
  capital_pool_id: string
  artifact_id: string
  event_time: datetime
  ingest_time: datetime | null
  payload: object
  trace_id: string
```

---

## 5. Commands

| Command | Input | Output | Notes |
|---|---|---|---|
| `SubmitExecutionRequest` | ready DeploymentPlan | execution_request_id | Called by SD-07 only。 |
| `AcceptExecutionRequest` | request_id | accepted | Runtime manager validates loader report。 |
| `CreateRuntimeBinding` | execution request | binding_id | Stored in Pantheon。 |
| `LoadArtifactIntoRuntime` | load request | load status | Performed by pantheon-lean bridge。 |
| `StartRuntime` | binding_id | runtime status | Creates paper/canary/live runtime。 |
| `PauseRuntime` | runtime_id + reason | action_id | High-risk for live。 |
| `LiquidateRuntime` | runtime_id + reason | action_id | Requires policy/RBAC/MFA unless kill-switch path。 |
| `ReplaceRuntimeArtifact` | runtime_id + new plan | new binding_id | Records rollback parent。 |
| `RestartRuntime` | runtime_id | status | Restricted by runtime_action_policy。 |
| `TerminateRuntime` | runtime_id | terminated | Audit required。 |
| `EmitExecutionTelemetry` | telemetry event | accepted/rejected | Called by pantheon-lean emitter。 |

---

## 6. Queries

| Query | Output |
|---|---|
| `GetExecutionRequest` | request status |
| `GetRuntimeBinding` | binding detail |
| `ListRuntimeBindings` | bindings by pool/stage/artifact |
| `GetRuntimeStatus` | current status |
| `GetRuntimeActions` | action history |
| `GetRuntimeTelemetrySummary` | heartbeat/order/fill/position summary |
| `GetRuntimeTrace` | trace from deployment plan to runtime events |

---

## 7. Events

```yaml
ExecutionRequestSubmitted:
  execution_request_id: string
  deployment_plan_id: string
  target_mode: string

ExecutionRequestAccepted:
  execution_request_id: string

RuntimeBindingCreated:
  binding_id: string
  runtime_id: string
  artifact_id: string
  capital_pool_id: string

ArtifactLoadStarted:
  load_request_id: string
  runtime_id: string
  artifact_id: string

ArtifactLoaded:
  load_request_id: string
  runtime_id: string
  artifact_id: string

RuntimeStateChanged:
  runtime_id: string
  from_state: string
  to_state: string
  reason: string

RuntimeActionRequested:
  action_id: string
  runtime_id: string
  action_type: string

RuntimeActionExecuted:
  action_id: string
  runtime_id: string
  action_type: string

ExecutionTelemetryEmitted:
  event_id: string
  runtime_id: string
  event_type: string
```

---

## 8. State machines

### 8.1 ExecutionRequest state

```text
submitted → accepted → loading → active
submitted → rejected
accepted / loading → failed
submitted / accepted → cancelled
```

### 8.2 RuntimeBinding state

```text
pending → loading → active → degraded → paused → replacing → active
active → terminated
active / loading → failed
```

### 8.3 RuntimeAction state

```text
requested → approved → executing → executed
requested → cancelled
approved / executing → failed
```

### 8.4 Runtime mode segregation

`paper`, `canary`, and `live` are not just labels. Each mode must use isolated:

```text
credential_ref
runtime state
artifact alias
capital_pool binding
telemetry stream
```

---

## 9. Hard invariants

1. `pantheon-lean` may consume only ready DeploymentPlans submitted via Pantheon execution gateway.
2. No persona, OpenClaw tool, or frontend command may call broker or LEAN execution path directly.
3. RuntimeBinding must reference deployment_plan_id, artifact_id, capital_pool_id, and runtime_id.
4. Live RuntimeBinding requires approval decision and loader report.
5. Broker credentials must be resolved only inside execution runtime boundary; never returned through Pantheon BFF.
6. Paper/canary/live runtime state and credentials must be segregated.
7. Runtime must emit canonical telemetry events for heartbeat, runtime state, orders, fills, positions, and broker connectivity.
8. Pause/liquidate/replace actions must be recorded as RuntimeAction and audit event.
9. Replace/rollback action must preserve rollback_parent.
10. `Lean` upstream code may be cherry-picked into `pantheon-lean`, but not used as parallel production execution substrate without a separate migration decision.
11. Execution request handling must be idempotent by deployment_plan_id and trace_id.
12. Kill switch / safe mode fast path may bypass normal approval latency but must still emit action and audit events.

---

## 10. Policy hooks

| Policy | Purpose |
|---|---|
| `runtime_action_policy` | Determines who can pause/restart/replace/liquidate。 |
| `runtime_mode_policy` | Enforces paper/canary/live segregation。 |
| `artifact_loader_policy` | Validates artifact type and runtime compatibility。 |
| `broker_capability_policy` | Ensures runtime action/order type supported。 |
| `telemetry_required_policy` | Defines mandatory event types and heartbeat interval。 |
| `safe_mode_policy` | Determines actions during risk_off / kill switch。 |
| `upstream_sync_policy` | Controls what changes can be backported from `Lean` to `pantheon-lean`。 |

---

## 11. Storage model

### Pantheon DB

```text
execution_requests
runtime_specs
runtime_bindings
runtime_status_snapshots
artifact_load_requests
runtime_actions
execution_events
execution_audit_actions
```

### pantheon-lean local/runtime state

```text
runtime_context.json
loaded_artifact_manifest.json
broker_session_state.json
runtime_health_snapshot.json
```

Runtime local state is not the global truth. Pantheon `runtime_bindings` and telemetry store remain governance truth.

---

## 12. API endpoints

### Pantheon execution API

```text
POST   /api/execution/requests
GET    /api/execution/requests/{execution_request_id}
POST   /api/execution/requests/{execution_request_id}/accept
POST   /api/execution/runtime-bindings
GET    /api/execution/runtime-bindings
GET    /api/execution/runtime-bindings/{binding_id}
GET    /api/execution/runtimes/{runtime_id}/status
POST   /api/execution/runtimes/{runtime_id}/pause
POST   /api/execution/runtimes/{runtime_id}/resume
POST   /api/execution/runtimes/{runtime_id}/restart
POST   /api/execution/runtimes/{runtime_id}/replace
POST   /api/execution/runtimes/{runtime_id}/liquidate
POST   /api/execution/runtimes/{runtime_id}/terminate
POST   /api/execution/telemetry/events
GET    /api/execution/runtimes/{runtime_id}/trace
```

### pantheon-lean bridge API / interface

```text
POST   /pantheon/runtime/accept-execution-request
POST   /pantheon/runtime/load-artifact
POST   /pantheon/runtime/start
POST   /pantheon/runtime/actions
POST   /pantheon/runtime/telemetry
GET    /pantheon/runtime/status/{runtime_id}
```

The exact transport may be HTTP, queue, or gRPC. The contract must remain stable.

---

## 13. Integration points

| Integration | Direction | Contract |
|---|---|---|
| SD-07 Promotion | read/command | ready DeploymentPlan creates ExecutionRequest。 |
| SD-06 Capital Pool | read | broker refs, risk policy, pool state。 |
| SD-09 Telemetry | write | canonical execution telemetry events。 |
| `pantheon-lean` | command/write | runtime bridge, artifact loader, telemetry emitter。 |
| `front-ai-trading-system` | read/command | runtime status and controlled actions。 |
| `Lean` upstream | read-only diff | backport source only。 |

---

## 14. Tests

### Pantheon unit tests

- SubmitExecutionRequest rejects plan not ready.
- RuntimeBinding requires artifact_id, pool_id, runtime_id.
- live action liquidate requires high-risk role or safe-mode trigger.
- duplicate execution request is idempotent.

### pantheon-lean bridge tests

- bridge rejects ExecutionRequest without loader_report_id.
- artifact loader records artifact_id and deployment_plan_id.
- telemetry emitter includes runtime_id, binding_id, pool_id, artifact_id.
- runtime action handler emits RuntimeActionExecuted.

### Integration tests

- DeploymentPlan ready → ExecutionRequest → RuntimeBinding → runtime active.
- Pause runtime command emits runtime action and state change.
- Replace runtime preserves rollback_parent.
- Broker credential never appears in Pantheon API response.

### Safety tests

- OpenClaw direct execution call is rejected.
- frontend cannot submit raw broker order.
- live RuntimeBinding cannot use paper credential_ref.

---

## 15. Definition of Done

1. Pantheon execution service can accept ready DeploymentPlan and create ExecutionRequest.
2. RuntimeBinding is persisted and queryable.
3. pantheon-lean bridge can load artifact context with runtime_binding_id / pool_id / artifact_id.
4. Runtime status and action events flow back to Pantheon.
5. Broker secret boundary is preserved.
6. Paper/canary/live segregation is enforced.
7. Tests cover runtime binding, telemetry metadata, and direct execution denial.

---

## 16. Codex task packets

### PTH-SD08-001 — Implement Pantheon execution service models

```text
Repo: ajoe734/pantheon
Target paths:
  services/execution/models.py
  docs/contracts/execution_request.schema.json
  docs/contracts/runtime_binding.schema.json
  docs/contracts/runtime_status.schema.json
Goal:
  Define ExecutionRequest, RuntimeBinding, RuntimeSpec, ArtifactLoadRequest, RuntimeStatus, RuntimeAction.
Acceptance tests:
  - RuntimeBinding requires deployment_plan_id/artifact_id/capital_pool_id/runtime_id
  - live RuntimeBinding requires deployment_mode=live and environment=live
  - credential values are not part of any model
```

### PTH-SD08-002 — Implement execution gateway and runtime binding store

```text
Repo: ajoe734/pantheon
Target paths:
  services/execution/execution_gateway.py
  services/execution/runtime_binding_store.py
  services/execution/repository.py
  services/execution/tests/test_execution_gateway.py
Goal:
  Accept ready DeploymentPlan, create ExecutionRequest, and persist RuntimeBinding.
Acceptance tests:
  - rejects deployment plan not in ready state
  - duplicate plan submission is idempotent
  - emits ExecutionRequestSubmitted and RuntimeBindingCreated
```

### PTH-SD08-003 — Implement pantheon-lean PantheonRuntimeBridge skeleton

```text
Repo: ajoe734/pantheon-lean
Target paths:
  Pantheon/RuntimeBridge/PantheonRuntimeBridge.cs
  Pantheon/RuntimeBridge/Models/ExecutionRequest.cs
  Pantheon/RuntimeBridge/Models/RuntimeBinding.cs
  Pantheon/RuntimeBridge/Tests/*
Goal:
  Add bridge models and request acceptance skeleton.
Acceptance tests:
  - rejects request without deployment_plan_id
  - rejects request without loader_report_id
  - maps accepted request to runtime context
Non-goals:
  - do not implement broker order logic in this task
```

### PTH-SD08-004 — Inject runtime context into pantheon-lean setup

```text
Repo: ajoe734/pantheon-lean
Target paths:
  Engine/Setup/BrokerageSetupHandler.cs
  Pantheon/RuntimeBridge/PantheonArtifactLoader.cs
  Pantheon/RuntimeBridge/PantheonRuntimeBindingEmitter.cs
Goal:
  Ensure runtime startup knows runtime_binding_id, capital_pool_id, artifact_id, deployment_plan_id.
Acceptance tests:
  - runtime context contains all four IDs
  - startup emits RuntimeBindingCreated/RuntimeStateChanged projection
  - missing context fails startup in Pantheon-managed mode
```

### PTH-SD08-005 — Implement canonical telemetry emitter

```text
Repo: ajoe734/pantheon-lean
Target paths:
  Pantheon/RuntimeBridge/PantheonTelemetryEmitter.cs
  Pantheon/RuntimeBridge/Models/TelemetryEvent.cs
  Pantheon/RuntimeBridge/Tests/*
Goal:
  Emit heartbeat, runtime state, order, fill, position, and broker connectivity events with canonical metadata.
Acceptance tests:
  - every emitted event includes runtime_id, binding_id, pool_id, artifact_id, trace_id
  - heartbeat interval is configurable
  - failed emit can be retried without duplicating event_id
```
