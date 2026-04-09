# OpenClaw Integration — Governance Mapping

Last updated: 2026-04-10
Owner: OSS-001 (Qwen)
Reviewer: Codex
Status: locked v1 governance boundary
Related: `OPENCLAW_RUNTIME_CONTRACT.md`, `OC-001`, `OC-002`, `OC-003`

## 1. Purpose

This document defines how the upstream OpenClaw runtime (`v2026.4.7`, SHA `5050017`) is governed by Pantheon's internal policy framework. OpenClaw is an **external agent runtime substrate**; Pantheon never delegates governance authority to it.

---

## 2. Governance Principle

> **OpenClaw provides execution capability. Pantheon provides governance authority.**

Every action that flows through OpenClaw must first pass through Pantheon's:
- Permission model (OC-001)
- Workflow orchestration (OC-002)
- StrategySpec normalization (OC-003)
- Promotion gate (REG-002)

---

## 3. Permission Governance (OC-001 Alignment)

### 3.1 Deny-First Model

OpenClaw's native tool/skill resolution is **wrapped** by Pantheon's deny-first permission contract:

```
Pantheon Request → Router (P4-001) → Permission Evaluator (OC-001)
  → If DENIED: reject before reaching OpenClaw
  → If ALLOWED: forward to openclaw-gateway-adapter → OpenClaw runtime
```

### 3.2 Mandatory Deny Rules

The following are **always denied**, regardless of OpenClaw's native capability:

| Rule | Rationale |
|---|---|
| Direct LEAN calls from OpenClaw tools | Live execution must flow REG → SIG → L, never O → L |
| Paper/backtest-only tools in live context | Promotion state must be checked |
| Cross-persona tool invocation without consult routing | Persona isolation boundary |
| Unaudited tool calls | All invocations must emit telemetry events |
| Secret/credential access outside persona auth profile | No implicit credential sharing |
| File system access outside per-agent workspace | Workspace isolation requirement |

### 3.3 Approval Hooks

Tools with `effect: "allow_with_approval"` require:
1. Human operator approval (or automated policy approval)
2. Audit record in feedback store
3. Session-scoped approval token (not global)

---

## 4. Workflow Governance (OC-002 Alignment)

### 4.1 Cron Workflow Mapping

| Pantheon Cron Workflow | OpenClaw Mechanism | Governance Constraint |
|---|---|---|
| `ingest` | Research task session | Output → governed `research_package` with lineage |
| `review` | Review/audit session | Output → `approval_request` with policy evaluation |
| `retrain` | Trainer session | Output → `registry_submission` with promotion gate |
| `deploy` | Workflow trigger | Must pass through REG-002 promotion gate |

### 4.2 Workflow Input/Output Contracts

Every workflow invocation must:
1. Receive a Pantheon-governed input payload (not raw OpenClaw input)
2. Produce an output that validates against Pantheon schema
3. Emit audit events for each lifecycle stage (started, completed, failed)

---

## 5. StrategySpec Normalization (OC-003 Alignment)

### 5.1 Normalization Pipeline

```
OpenClaw Workflow Output
  → openclaw-gateway-adapter captures raw payload
  → normalize into `StrategySpec` + `WorkflowHandoff`
  → validate against `services/control-plane/specs/strategy_spec.schema.json`
  → attach governance metadata (registry_id, lineage, checksum)
  → submit to registry (REG-001/REG-002)
```

### 5.2 Governance Metadata Attachment

Every normalization produces **two** canonical artifacts:

1. **`StrategySpec`** — the strategy definition (validated against `strategy_spec.schema.json`).
   Carries `governance` and `provenance` as defined in OC-003.
2. **`WorkflowHandoff`** — the registry/execution envelope (validated against `workflow_handoff.schema.json`).
   Wraps the `StrategySpec` and carries `registry_hints`, `governance_context`, and handoff `provenance`.

The `WorkflowHandoff` carries the adapter governance metadata:

```json
{
  "handoff_version": "1.0",
  "handoff_id": "handoff-<strategy_id>",
  "handoff_type": "strategy_spec",
  "from_stage": "research_ingest",
  "to_stage": "replication_gate",
  "created_at": "<RFC 3339 timestamp>",
  "strategy_spec": { "<canonical StrategySpec>" },
  "registry_hints": {
    "artifact_type": "strategy_spec",
    "initial_lifecycle_state": "draft",
    "lineage_ref": "<upstream_research_id>"
  },
  "governance_context": {
    "approval_required": true,
    "execution_context": "research",
    "policy_id": "<OC-001 profile ID>",
    "risk_profile": "<risk profile string>"
  },
  "provenance": {
    "created_by": "<agent name>",
    "created_at": "<RFC 3339 timestamp>",
    "source_task_id": "<task ID>",
    "source_channel": "ingest | review | retrain | deploy",
    "source_persona": "<persona name>"
  }
}
```

Key field naming aligned to the canonical `WorkflowHandoff` schema:
- `registry_hints.initial_lifecycle_state` (not `lifecycle_state`) — enum values: `draft`, `candidate`.
- `governance_context` belongs on `WorkflowHandoff`, **not** on `StrategySpec`.
- `StrategySpec` carries its own `governance` and `provenance` objects, independent of the handoff envelope.

---

## 6. Error Governance

### 6.1 Error Escalation Ladder

| Error Layer | Pantheon Response |
|---|---|---|
| Known typed errors | Map to internal error domain, retry or fail as appropriate |
| Transport/system errors | Exponential backoff, alert if persistent |
| `unknown_upstream_error` | **Maximum governance response** — isolate, degrade, escalate |

### 6.2 Unknown Error Governance Chain

When OpenClaw produces an `unknown_upstream_error`:

1. **Record** — full envelope preserved in telemetry store
2. **Degrade** — session marked `degraded`, no new work accepted
3. **Circuit break** — after threshold, agent session isolated
4. **Incident** — auto-open incident if pattern persists
5. **No live impact** — OpenClaw errors must never affect live execution kernel (LEAN)

### 6.3 Kill-Switch Independence

> **The kill-switch fast path must never depend on OpenClaw availability.**

Kill-switch flows through `runtime-manager` directly, bypassing OpenClaw entirely. This is a core safety invariant.

---

## 7. Audit and Telemetry

### 7.1 Required Audit Events

All events listed in `OPENCLAW_RUNTIME_CONTRACT.md` §11 must be:
- Emitted by the adapter
- Written to the telemetry store
- Queryable by `persona_id`, `session_id`, and time range
- Retained per the platform's audit retention policy

### 7.2 Telemetry Isolation

OpenClaw runtime telemetry (internal metrics, logs, traces) is **separate** from Pantheon's canonical telemetry store. The adapter only emits:
- Governance events (§7.1)
- Error events (§6)
- Workflow lifecycle events

OpenClaw's internal diagnostics are **not** part of Pantheon's canonical telemetry.

---

## 8. Upgrade Governance

When the pinned OpenClaw version changes:

1. **Pre-upgrade**: Run smoke-test suite against new version
2. **Permission review**: Confirm deny rules still apply to new version's capabilities
3. **Schema review**: Confirm StrategySpec normalization still produces valid output
4. **Governance sign-off**: Reviewer (Codex) must approve the version change
5. **Activity log**: Record pin change in `ai-activity-log.jsonl`

---

## 9. Relationship to Other Governance Documents

| Document | Relationship |
|---|---|
| `OPENCLAW_RUNTIME_CONTRACT.md` | Upstream runtime boundary — this document adds Pantheon governance overlay |
| `OC-001` (Permission model) | This document maps OpenClaw tools into OC-001 deny-first contract |
| `OC-002` (Cron workflows) | This document constrains OpenClaw workflow outputs with Pantheon promotion gates |
| `OC-003` (StrategySpec) | This document defines the normalization pipeline from OpenClaw output to StrategySpec |
| `REG-002` (Promotion gate) | All OpenClaw-originating artifacts must pass REG-002 before paper/live promotion |
| `PAPER_CANARY_LIVE_POLICY.md` | OpenClaw has no authority to change promotion state directly |
