---
project: Pantheon
document_type: P0 System Design / Architecture Decision / Codex Implementation Packet
language: zh-TW
status: implemented
revision: v2
baseline: >
  Based on Pantheon consolidated blueprint and latest implementation correction:
  current actual LEAN bridge is `pantheon/lean` submodule, remote `ajoe734/pantheon-lean.git`;
  `lean-platform` is not the current Pantheon execution target.
---

# SD-P0-03 — RuntimeBinding Context Propagation

## 1. Purpose

本 SD 定義 `RuntimeBinding` 如何從 Pantheon control / registry / runtime layer 傳遞到目前實際 bridge：

```text
pantheon
→ runtime_bootstrap.py
→ pantheon/lean submodule
→ PantheonAlgoBase
→ Paper Runtime TelemetryEvent
```

`RuntimeBinding` 是 Pantheon runtime trace 的 pivot。
沒有 RuntimeBinding，後續 telemetry、reconciliation、incident、postmortem、evolution 都無法可靠歸因。

---

## 2. Current Facts

```text
pantheon/lean submodule exists.
remote = ajoe734/pantheon-lean.git.
PantheonAlgoBase exists.
runtime_bootstrap.py starts paper runtime baseline.
live role is health-only sidecar.
TelemetryEvent schema expects binding_id, runtime_id, capital_pool_id, artifact_id, plan_id, deployment_stage.
```

目前需明確設計：

```text
RuntimeBinding context 如何注入 runtime_bootstrap？
RuntimeBinding context 如何進 PantheonAlgoBase？
Paper runtime 如何取得 context？
Telemetry exporter 如何讀 context？
```

---

## 3. Target Data Flow

```text
DeploymentPlan approved for paper
→ RuntimeBinding created
→ RuntimeBootstrapRequest includes binding context
→ runtime_bootstrap.py reads context
→ PantheonRuntimeContext object created
→ PantheonAlgoBase receives or can query context
→ telemetry emitter attaches context to all events
```

---

## 4. Domain Objects

### 4.1 RuntimeBinding

```json
{
  "runtime_binding_id": "rtb-...",
  "runtime_id": "rt-...",
  "deployment_plan_id": "dp-...",
  "artifact_id": "art-...",
  "artifact_version": "1.0.0",
  "artifact_checksum": "sha256:...",
  "strategy_id": "strat-...",
  "capital_pool_id": "pool-...",
  "persona_capital_binding_id": "pcb-...",
  "deployment_stage": "paper",
  "runtime_role": "paper",
  "runtime_state": "created|loading|active|degraded|paused|terminated",
  "engine_bridge_repo": "ajoe734/pantheon-lean.git",
  "engine_bridge_path": "pantheon/lean",
  "engine_bridge_commit": "...",
  "launch_manifest_hash": "sha256:...",
  "effective_at": "RFC3339",
  "retired_at": null,
  "rollback_parent": null
}
```

### 4.2 PantheonRuntimeContext

This is the object exposed to runtime code.

```json
{
  "runtime_binding_id": "rtb-...",
  "runtime_id": "rt-...",
  "deployment_plan_id": "dp-...",
  "deployment_stage": "paper",
  "runtime_role": "paper",
  "artifact": {
    "artifact_id": "art-...",
    "artifact_version": "1.0.0",
    "artifact_checksum": "sha256:...",
    "strategy_id": "strat-..."
  },
  "capital": {
    "capital_pool_id": "pool-...",
    "persona_capital_binding_id": "pcb-..."
  },
  "bridge": {
    "repo": "ajoe734/pantheon-lean.git",
    "path": "pantheon/lean",
    "commit": "...",
    "runtime_adapter_version": "0.1.0"
  },
  "trace": {
    "trace_id": "uuid",
    "correlation_id": "uuid"
  }
}
```

### 4.3 Context Source Modes

```text
context_source:
  - launch_manifest
  - env_vars
  - local_dev_seed
  - unavailable
```

P0 target:

```text
launch_manifest preferred
env_vars acceptable for dev smoke
local_dev_seed only for dev tests
unavailable must mark telemetry as unverifiable or fail
```

---

## 5. Context Injection Methods

### 5.1 Preferred: launch manifest

```text
runtime_bootstrap.py --launch-manifest /path/to/manifest.json
```

Pros:

```text
- versioned
- auditable
- hashable
- testable
```

### 5.2 Acceptable P0 fallback: env vars

```text
PANTHEON_RUNTIME_BINDING_ID
PANTHEON_DEPLOYMENT_PLAN_ID
PANTHEON_RUNTIME_ID
PANTHEON_ARTIFACT_ID
PANTHEON_ARTIFACT_VERSION
PANTHEON_CAPITAL_POOL_ID
PANTHEON_DEPLOYMENT_STAGE
PANTHEON_ENGINE_BRIDGE_COMMIT
```

Env vars are acceptable for dev / paper smoke only.

### 5.3 Not acceptable for production

```text
hardcoded runtime ids
frontend-provided runtime ids
OpenClaw-provided runtime ids
artifact payload embedded binding ids
```

---

## 6. PantheonAlgoBase Integration Contract

`PantheonAlgoBase` should expose or support:

```python
class PantheonAlgoBase:
    def get_pantheon_context(self) -> PantheonRuntimeContext: ...
    def emit_pantheon_event(self, event_type: str, metrics: dict, metadata: dict | None = None): ...
```

Minimum P0 behavior:

```text
1. context is loaded at initialization.
2. missing context in paper dev can degrade, but must be visible.
3. missing context in staging/prod fails closed for deployment-managed runtime.
4. emitted events attach context automatically.
```

---

## 7. Commands

```text
CreateRuntimeBinding
AttachRuntimeContext
ValidateRuntimeContext
StartRuntimeWithContext
EmitRuntimeContextLoaded
MarkRuntimeContextMissing
```

---

## 8. Events

```text
RuntimeBindingCreated
RuntimeContextMaterialized
RuntimeContextLoaded
RuntimeContextMissing
RuntimeContextInvalid
RuntimeContextAttachedToEvent
```

### Event example

```json
{
  "event_id": "uuid",
  "event_type": "RuntimeContextLoaded",
  "event_time": "RFC3339",
  "runtime_binding_id": "rtb-...",
  "runtime_id": "rt-...",
  "deployment_plan_id": "dp-...",
  "deployment_stage": "paper",
  "engine_bridge_commit": "..."
}
```

---

## 9. Hard Invariants

```text
INV-CTX-001:
  RuntimeBinding is the canonical identity of a runtime deployment.

INV-CTX-002:
  A deployment-managed runtime MUST carry RuntimeBinding context.

INV-CTX-003:
  Runtime telemetry MUST carry binding_id when RuntimeBinding exists.

INV-CTX-004:
  RuntimeBinding.deployment_stage MUST match TelemetryEvent.deployment_stage.

INV-CTX-005:
  RuntimeBinding.artifact_id MUST match TelemetryEvent.artifact_id.

INV-CTX-006:
  RuntimeBinding.capital_pool_id MUST match TelemetryEvent.capital_pool_id.

INV-CTX-007:
  live runtime cannot start with context_source=unavailable.

INV-CTX-008:
  paper dev runtime may degrade with context_source=local_dev_seed, but BFF/UI must show unverifiable/dev mode.

INV-CTX-009:
  RuntimeBinding must reference current official bridge repo: pantheon/lean / pantheon-lean.

INV-CTX-010:
  runtime context must never include raw broker secrets.
```

---

## 10. Policy-configurable Rules

```text
1. Whether paper runtime may run with dev_seed context.
2. Whether staging requires launch_manifest only.
3. Which fields are mandatory for paper vs canary vs live.
4. Whether missing persona_capital_binding_id blocks paper.
5. Telemetry degradation policy for missing optional fields.
```

Recommended:

```text
dev:
  env_vars or local_dev_seed allowed for paper only

staging:
  launch_manifest required

prod:
  launch_manifest required
  signed manifest required
```

---

## 11. Failure Behavior

| Condition | dev paper | staging paper | canary/live |
|---|---|---|---|
| missing runtime_binding_id | degraded / test-only | fail closed | fail closed |
| missing artifact_id | degraded | fail closed | fail closed |
| missing capital_pool_id | degraded | fail closed | fail closed |
| missing bridge_commit | degraded | degraded | fail closed |
| stage mismatch | fail | fail | fail |
| raw broker secret present | fail | fail | fail |

---

## 12. Tests

### Unit tests

```text
test_runtime_context_loads_from_manifest
test_runtime_context_loads_from_env_for_dev
test_runtime_context_rejects_missing_binding_in_staging
test_runtime_context_rejects_stage_mismatch
test_runtime_context_does_not_include_secrets
```

### Bridge tests

```text
test_pantheon_algo_base_exposes_context
test_pantheon_algo_base_event_attaches_context
test_pantheon_algo_base_missing_context_marks_unverifiable
```

### Telemetry tests

```text
test_heartbeat_includes_binding_id
test_telemetry_artifact_matches_runtime_context
test_telemetry_stage_matches_runtime_context
test_telemetry_pool_matches_runtime_context
```

---

## 13. Non-goals

```text
1. Do not implement live broker execution.
2. Do not require full production manifest signing in dev.
3. Do not migrate to lean-platform.
4. Do not make frontend supply runtime context.
5. Do not make OpenClaw supply runtime context.
6. Do not implement full reconciliation in this SD.
```

---

## 14. Acceptance Criteria

```text
AC-CTX-001:
  RuntimeBinding context can be materialized into a runtime context object.

AC-CTX-002:
  runtime_bootstrap paper role can receive context.

AC-CTX-003:
  PantheonAlgoBase can access context.

AC-CTX-004:
  emitted paper heartbeat includes runtime_binding_id.

AC-CTX-005:
  missing context fails closed in non-dev managed runtime.

AC-CTX-006:
  no raw secret in context.

AC-CTX-007:
  tests cover env var and manifest source modes.
```

---

## 15. Implementation Evidence

### P0-CTX-002 — Wire runtime_bootstrap.py to manifest/env runtime context

**Status:** implemented (Task-ID: P0-CTX-002, Owner: Claude, Reviewer: Codex)

**Delivered files:**
- `services/execution/lean_runtime/runtime_bootstrap.py` — wired to `PantheonRuntimeContext`; paper role loads context from `--launch-manifest` or env vars; staging/canary/live missing-context fails closed with `SystemExit(2)`; live/sidecar roles remain health-only
- `services/execution/lean_runtime/paper_runtime.py` — `RuntimeBindingResolver` accepts `PantheonRuntimeContext` as pre-loaded binding fallback; `_runtime_context_snapshot()` exposes loaded context in health payload
- `services/execution/lean_runtime/test_runtime_bootstrap.py` — 6 tests covering manifest load, env load, staging fail-closed, live sidecar fail-closed, live broker guard

**Verification:**
```
pytest services/execution/lean_runtime/test_runtime_bootstrap.py -v
# 6 passed
```

**Acceptance satisfied:**
- AC-CTX-002: runtime_bootstrap paper role receives PantheonRuntimeContext ✓
- AC-CTX-005: missing context fails closed for staging/canary/live managed runtime ✓
- AC-CTX-007: tests cover env var and manifest source modes ✓

---

## 16. Codex Task Packets

### TP-CTX-001 — Add PantheonRuntimeContext model

```yaml
task_id: TP-CTX-001
repo: pantheon
goal: Define PantheonRuntimeContext model and validation.
target_paths:
  - services/execution/lean_runtime/runtime_context.py
  - services/execution/lean_runtime/tests/test_runtime_context.py
acceptance:
  - validates required fields
  - rejects secrets
  - supports manifest/env source modes
```

### TP-CTX-002 — Wire runtime_bootstrap to context

```yaml
task_id: TP-CTX-002
repo: pantheon
goal: runtime_bootstrap reads context from manifest or env.
target_paths:
  - services/execution/lean_runtime/runtime_bootstrap.py
acceptance:
  - paper role receives context
  - live role still fail-closed
```

### TP-CTX-003 — Add PantheonAlgoBase context access

```yaml
task_id: TP-CTX-003
repo: pantheon
target_submodule: pantheon/lean
goal: Ensure PantheonAlgoBase can access PantheonRuntimeContext.
target_paths:
  - pantheon/lean/pantheon_algo/base.py
acceptance:
  - exposes get_pantheon_context
  - context can be used by telemetry emitter
non_goals:
  - do not enable live broker execution
```

### TP-CTX-004 — Add telemetry context attachment test

```yaml
task_id: TP-CTX-004
repo: pantheon
goal: Verify emitted paper event carries RuntimeBinding context.
target_paths:
  - services/telemetry/tests/*
  - services/execution/lean_runtime/tests/*
acceptance:
  - heartbeat contains runtime_binding_id
  - stage matches context
```
