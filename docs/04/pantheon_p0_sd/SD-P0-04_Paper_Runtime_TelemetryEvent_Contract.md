---
project: Pantheon
document_type: P0 System Design / Architecture Decision / Codex Implementation Packet
language: zh-TW
status: implemented
revision: v1
baseline: >
  Based on Pantheon consolidated blueprint and latest implementation correction:
  current actual LEAN bridge is `pantheon/lean` submodule, remote `ajoe734/pantheon-lean.git`;
  `lean-platform` is not the current Pantheon execution target.
---

# SD-P0-04 — Paper Runtime TelemetryEvent Contract

## 1. Purpose

本 SD 定義 paper runtime baseline 要輸出的 Pantheon canonical telemetry。
目標是先完成 paper-only telemetry loop：

```text
runtime_bootstrap.py paper role
→ pantheon/lean / PantheonAlgoBase
→ Paper Runtime TelemetryEvent
→ pantheon telemetry ingest
→ runtime status projection
```

本文件不要求 live broker telemetry，也不要求 full reconciliation；但會為後續 reconciliation 提供足夠 event identity。

---

## 2. Current Facts

```text
Pantheon TelemetryEvent schema already expects:
  event_id
  event_type
  created_at
  execution_mode
  binding_id
  runtime_id
  capital_pool_id
  artifact_id
  artifact_version
  deployment_stage
  plan_id
  persona_capital_binding_id
  target
  metrics

Current runtime:
  paper baseline exists
  live role is health-only sidecar
  bracket order is log-only
  full broker SDK production kernel is not active
```

P0 要做的是：先讓 paper runtime 產生最低限度的 valid telemetry。

---

## 3. Telemetry Scope

### 3.1 In scope P0 event types

```text
heartbeat
deploy_started
deploy_completed
paper_order_simulated
paper_fill_simulated
pnl_snapshot
drawdown_snapshot
runtime_health
order_rejection_simulated
bracket_order_logged
```

### 3.2 Out of scope P0 event types

```text
live_order_submitted
live_fill_received
broker_liquidation_submitted
broker_bracket_order_submitted
canary_live_pnl
real_margin_call
```

---

## 4. Event Producer

Current P0 producers:

```text
services/execution/lean_runtime/runtime_bootstrap.py
paper runtime executor
pantheon/lean PantheonAlgoBase
```

Future producers:

```text
Lean Launcher result handler
broker adapter
sidecar telemetry collector
```

---

## 5. TelemetryEvent P0 Contract

### 5.1 Required fields

```json
{
  "event_id": "uuid",
  "event_type": "heartbeat",
  "created_at": "RFC3339",
  "execution_mode": "paper",
  "binding_id": "rtb-...",
  "runtime_id": "rt-...",
  "capital_pool_id": "pool-...",
  "artifact_id": "art-...",
  "artifact_version": "1.0.0",
  "deployment_stage": "paper",
  "plan_id": "dp-...",
  "persona_capital_binding_id": "pcb-...",
  "target": {
    "strategy_id": "strat-...",
    "artifact_type": "execution_bundle",
    "artifact_version": "1.0.0",
    "lineage_ref": "lin-..."
  },
  "metrics": {
    "heartbeat": 1
  },
  "metadata": {
    "engine_bridge_repo": "ajoe734/pantheon-lean.git",
    "engine_bridge_path": "pantheon/lean",
    "engine_bridge_commit": "...",
    "runtime_role": "paper",
    "context_source": "launch_manifest|env_vars|local_dev_seed"
  }
}
```

### 5.2 Event type mappings

| Runtime event | TelemetryEvent.event_type | Required metrics |
|---|---|---|
| paper runtime alive | `heartbeat` | `heartbeat: 1` |
| paper runtime start | `deploy_started` | `action: deploy_started` |
| paper runtime ready | `deploy_completed` | `action: deploy_completed` |
| simulated order | `paper_order_simulated` | `action`, `order_quantity`, optional |
| simulated fill | `paper_fill_simulated` | `fill_quantity`, `fill_price` |
| pnl update | `pnl_snapshot` | `pnl` |
| drawdown update | `drawdown_snapshot` | `drawdown_pct` |
| order rejected in sim | `order_rejection` | `reject_reason` |
| bracket order logged only | `bracket_order_logged` | `action: bracket_logged_only` |

---

## 6. Runtime Health Projection

Paper telemetry should support a minimal runtime status:

```json
{
  "runtime_id": "rt-...",
  "runtime_binding_id": "rtb-...",
  "deployment_stage": "paper",
  "state": "active|degraded|terminated",
  "last_heartbeat_at": "RFC3339",
  "health_summary": {
    "paper_runtime": "ok",
    "bridge": "ok",
    "telemetry": "ok|degraded",
    "broker": "not_applicable"
  },
  "engine_bridge_repo": "ajoe734/pantheon-lean.git",
  "engine_bridge_commit": "..."
}
```

---

## 7. Commands

```text
EmitPaperHeartbeat
EmitDeployStarted
EmitDeployCompleted
EmitPaperFillSimulated
EmitPnlSnapshot
EmitDrawdownSnapshot
EmitBracketOrderLoggedOnly
```

---

## 8. Events

Events are instances of `TelemetryEvent`.

Additional internal events:

```text
TelemetryEmitRequested
TelemetryEmitSucceeded
TelemetryEmitFailed
TelemetryValidationFailed
RuntimeProjectionUpdated
```

---

## 9. Hard Invariants

```text
INV-TEL-PAPER-001:
  Paper runtime telemetry MUST use deployment_stage=paper.

INV-TEL-PAPER-002:
  Paper runtime telemetry MUST NOT claim live or canary stage.

INV-TEL-PAPER-003:
  Every managed paper runtime event MUST include binding_id when binding exists.

INV-TEL-PAPER-004:
  Every telemetry event MUST include engine_bridge_repo and engine_bridge_commit in metadata once available.

INV-TEL-PAPER-005:
  bracket_order_logged MUST NOT be confused with broker order submission.

INV-TEL-PAPER-006:
  paper_fill_simulated MUST NOT be reported as live fill.

INV-TEL-PAPER-007:
  raw broker secrets MUST NOT appear in telemetry metadata.

INV-TEL-PAPER-008:
  duplicate event_id MUST be idempotently ignored by ingest.

INV-TEL-PAPER-009:
  telemetry with deployment_stage mismatch MUST be rejected.

INV-TEL-PAPER-010:
  telemetry without required binding fields in staging/prod MUST be rejected.
```

---

## 10. Policy-configurable Rules

```text
1. Heartbeat interval.
2. Whether dev paper telemetry can run with local_dev_seed context.
3. Whether missing persona_capital_binding_id is degraded or blocking in dev.
4. Retry count for telemetry emitter.
5. DLQ behavior.
6. Projection update frequency.
```

Recommended defaults:

```text
heartbeat_interval_seconds: 30
retry_count: 3
dev_missing_binding_behavior: degraded
staging_missing_binding_behavior: reject
```

---

## 11. Failure Behavior

| Failure | Behavior |
|---|---|
| telemetry endpoint unavailable | retry, then DLQ |
| missing binding_id in dev paper | mark unverifiable / degraded |
| missing binding_id in staging/prod | reject |
| deployment_stage != paper for paper runtime | reject |
| duplicate event_id | dedupe |
| bracket order emitted as live order | reject |
| raw secret found | reject and audit |

---

## 12. Ingest Requirements

Telemetry ingest must:

```text
1. validate schema
2. validate event_id idempotency
3. validate binding_id exists if required
4. validate deployment_stage matches binding
5. write telemetry store
6. update runtime projection
7. write lineage edge if available
8. expose status to BFF
```

---

## 13. Tests

### Producer tests

```text
test_paper_heartbeat_event_shape
test_deploy_started_event_shape
test_deploy_completed_event_shape
test_paper_fill_simulated_not_live_fill
test_bracket_order_logged_not_submitted
test_event_contains_bridge_repo_and_commit
```

### Ingest tests

```text
test_valid_paper_heartbeat_accepted
test_missing_binding_rejected_in_staging
test_stage_mismatch_rejected
test_duplicate_event_id_deduped
test_bracket_logged_only_accepted_as_non_broker_event
```

### Projection tests

```text
test_heartbeat_updates_runtime_summary
test_deploy_completed_sets_runtime_active
test_missing_heartbeat_sets_degraded
```

---

## 14. Non-goals

```text
1. Do not implement live broker fills.
2. Do not implement canary telemetry.
3. Do not submit bracket orders to broker.
4. Do not implement full reconciliation.
5. Do not enable OpenClaw broker action.
6. Do not migrate telemetry producer to lean-platform.
```

---

## 15. Acceptance Criteria

```text
AC-TEL-001:
  paper runtime can emit heartbeat that passes TelemetryEvent validation.

AC-TEL-002:
  telemetry event includes binding_id when RuntimeBinding exists.

AC-TEL-003:
  telemetry event includes engine_bridge_repo and commit metadata.

AC-TEL-004:
  bracket order log-only event cannot be mistaken for broker order.

AC-TEL-005:
  BFF runtime status projection can show last paper heartbeat.

AC-TEL-006:
  duplicate telemetry event id is idempotent.

AC-TEL-007:
  live broker telemetry remains out of scope and disabled.
```

---

## 16. Codex Task Packets

### TP-TEL-001 — Add paper telemetry emitter

```yaml
task_id: TP-TEL-001
repo: pantheon
goal: Implement paper runtime telemetry emitter.
target_paths:
  - services/execution/lean_runtime/paper_telemetry.py
  - services/execution/lean_runtime/tests/test_paper_telemetry.py
acceptance:
  - emits heartbeat
  - emits deploy_started/deploy_completed
  - includes bridge metadata
non_goals:
  - no live broker telemetry
```

### TP-TEL-002 — Wire runtime_bootstrap heartbeat

```yaml
task_id: TP-TEL-002
repo: pantheon
goal: runtime_bootstrap paper role emits heartbeat.
target_paths:
  - services/execution/lean_runtime/runtime_bootstrap.py
acceptance:
  - heartbeat event emitted or scheduled
  - runtime status projection updated
```

### TP-TEL-003 — Add telemetry ingest validation tests

```yaml
task_id: TP-TEL-003
repo: pantheon
goal: Validate paper TelemetryEvent schema and stage matching.
target_paths:
  - services/telemetry/tests/test_paper_runtime_events.py
acceptance:
  - missing binding rejected where required
  - duplicate event deduped
  - stage mismatch rejected
```

### TP-TEL-004 — Add bracket_order_logged event

```yaml
task_id: TP-TEL-004
repo: pantheon
goal: Represent log-only bracket order as non-broker telemetry event.
target_paths:
  - services/execution/*
  - services/telemetry/*
acceptance:
  - event_type bracket_order_logged
  - not treated as broker submitted order
```

---

## 17. P0-TEL-001 Implementation Evidence

`P0-TEL-001` implements the paper-only telemetry baseline from this SD:

```text
producer:
  services/execution/lean_runtime/paper_runtime.py

tests:
  services/execution/lean_runtime/test_paper_runtime.py
  services/telemetry/test_paper_runtime_ingest_contract.py
  services/telemetry/test_main_routes.py
  services/execution/lean_runtime/test_runtime_bootstrap.py
  services/execution/lean_runtime/test_runtime_context.py
  services/telemetry/test_capture.py
  lean/Algorithm.Python/pantheon_algo/test_base.py

schema:
  services/telemetry/telemetry_event.schema.json
```

Delivered behavior:

```text
1. RuntimeTelemetryEmitter builds paper TelemetryEvent envelopes for heartbeat,
   deploy_started, deploy_completed, pnl_snapshot, paper_fill_simulated, and
   bracket_order_logged.
2. Events include binding_id, runtime_id, capital_pool_id, artifact_id,
   artifact_version, plan_id, persona_capital_binding_id, authority_refs, target,
   and bridge metadata when the runtime context exposes it.
3. Paper producer rejects non-paper deployment_stage before emission.
4. Telemetry ingest dedupes repeated event_id.
5. Telemetry ingest rejects missing binding_id and rejects deployment_stage
   mismatch against the RuntimeBinding store.
6. bracket_order_logged remains logged_only with submitted_to_broker=false and is
   not treated as broker submission.
```

Closeout verification on 2026-05-01:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest services.execution.lean_runtime.test_paper_runtime services.telemetry.test_paper_runtime_ingest_contract
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest services.telemetry.test_main_routes services.execution.lean_runtime.test_runtime_bootstrap services.execution.lean_runtime.test_runtime_context
cd services/telemetry && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_capture
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/lupin/code/pantheon:/home/lupin/code/pantheon/lean/Algorithm.Python python3 lean/Algorithm.Python/pantheon_algo/test_base.py
```
