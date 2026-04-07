# Trajectory and Preference Store Contract

**Task:** FB-001  
**Owner:** Codex  
**Reviewer:** Claude  
**Status:** APPROVED for v1 lock

Review record: `services/feedback/schema/review_fb001_fb002_claude.md`

---

## 1. Purpose

The trajectory and preference store is the governed memory of how strategies were judged and how they behaved.

It exists so that:

- trader approvals, edits, rejections, and rationales become structured learning data
- execution telemetry is persisted independently from live execution state
- feedback can be joined back to registry entries, strategy versions, and promotion states
- later optimizers and learning workflows do not need to scrape chat logs or broker state directly

This contract defines the storage-facing event shapes before storage backend and indexing choices are finalized.

Machine-readable schemas:

- `services/feedback/schema/trader_feedback_event.schema.json`
- `services/feedback/schema/execution_telemetry_event.schema.json`

---

## 2. Design Principles

### 2.1 Event-sourced, not mutable state

The store should capture immutable events, not overwrite a single "current opinion" document.

Examples:

- trader approved a candidate
- trader edited a thesis
- system observed slippage on a paper run
- live execution emitted a fill or drawdown snapshot

Derived summaries can be materialized later, but raw events remain append-only.

### 2.2 Governance first

Feedback and telemetry must never be interpreted as permission to mutate live strategy state directly.

The store is allowed to influence:

- evaluators
- critics
- preference-learning pipelines
- optimizer inputs

The store is not allowed to trigger:

- live promotion
- direct LEAN mutation
- direct policy replacement

### 2.3 Registry linkage is mandatory

Every event must be linkable back to the governed artifact that was being judged or executed.

Minimum linkage:

- `registry_id`
- `strategy_id`
- `artifact_version`
- `artifact_type`
- `promotion_state`

If one of these is unknown at event time, it should be omitted explicitly rather than silently inferred.

---

## 3. Event Families

The store contains two top-level families.

| Family | Purpose | Follow-up task |
|---|---|---|
| `trader_feedback` | explicit human approve/edit/reject/rationale feedback | FB-002 |
| `execution_telemetry` | pnl, drawdown, slippage, fills, order outcomes | FB-003 |

These families intentionally stay separate so we do not confuse human preference signals with market execution observations.

---

## 4. Shared Linkage Object

Both families use the same governed linkage object.

| Field | Required | Description |
|---|---|---|
| `registry_id` | no | exact registry entry being judged or executed |
| `strategy_id` | yes | stable strategy family id |
| `artifact_version` | no | version string tied to registry entry |
| `artifact_type` | no | `strategy_spec`, `model_artifact`, `signal_snapshot`, `execution_bundle`, etc. |
| `promotion_state` | no | `draft`, `candidate`, `paper`, `live`, `retired` |
| `lineage_ref` | no | optional lineage pointer such as parent registry id or run id |

This is the minimum join surface for:

- REG-001 / REG-002
- evaluators
- future MLflow or W&B integration

---

## 5. Trader Feedback Events

Trader feedback events capture explicit human judgment.

### 5.1 Minimum event types

| Event type | Meaning |
|---|---|
| `approve` | reviewer accepted the artifact or recommendation |
| `edit` | reviewer changed part of the recommendation |
| `reject` | reviewer rejected the recommendation |
| `rationale` | free-form explanatory feedback without a decision transition |

### 5.2 Required properties

| Field | Required | Description |
|---|---|---|
| `event_id` | yes | unique event id |
| `event_type` | yes | one of the feedback event types above |
| `created_at` | yes | RFC3339 timestamp |
| `actor_id` | yes | stable reviewer/operator id |
| `actor_role` | yes | `operator`, `approver`, or `system` to stay aligned with OC-001 |
| `channel` | yes | source channel such as `console`, `web`, `telegram` |
| `target` | yes | governed linkage object from §4 |
| `task_ref` | no | workflow task or review id |
| `decision_ref` | no | approval workflow id |
| `rationale` | no | free-form explanation |
| `edits` | no | structured edit payload for `edit` events |

### 5.3 Important boundary

`edit` is not a silent overwrite of the artifact.
It is an event describing a human correction.

Any materialized updated artifact must still flow back through registry and promotion gates.

---

## 6. Execution Telemetry Events

Execution telemetry captures how the governed artifact behaved after approval.

Draft artifacts are intentionally excluded from execution telemetry even though
`draft` remains valid in the shared linkage object. Draft artifacts must not
reach executable surfaces, so telemetry begins once an artifact reaches
candidate, paper, or live evaluation paths.

### 6.1 Minimum telemetry event types

| Event type | Meaning |
|---|---|
| `pnl_snapshot` | pnl observation at a point in time |
| `drawdown_snapshot` | drawdown observation |
| `slippage_observation` | measured slippage for an order or batch |
| `fill_observation` | fill-level execution result |
| `order_rejection` | order rejected or failed |

### 6.2 Required properties

| Field | Required | Description |
|---|---|---|
| `event_id` | yes | unique event id |
| `event_type` | yes | one of the telemetry event types above |
| `created_at` | yes | RFC3339 timestamp |
| `execution_mode` | yes | `paper` or `live` |
| `target` | yes | governed linkage object from §4 |
| `broker` | no | broker or exchange identifier |
| `account_ref` | no | account or sleeve identifier |
| `signal_id` | no | originating signal id |
| `run_id` | no | batch / rebalance / execution run id |
| `metrics` | yes | metrics payload specific to the event type |

### 6.3 Metrics payload expectations

The `metrics` object must be flexible, but at least one numeric or categorical outcome must be present.

Examples:

- `pnl`
- `drawdown_pct`
- `slippage_bps`
- `fill_quantity`
- `fill_price`
- `reject_reason`

---

## 7. Storage Expectations

Storage backend is open, but the logical guarantees are not.

Required guarantees:

1. append-only raw event storage
2. stable event ids
3. ability to query by:
   - `strategy_id`
   - `registry_id`
   - `promotion_state`
   - `event_type`
   - `created_at`
4. no hard coupling to LEAN runtime memory or broker-specific tables

---

## 8. Review Outcome

Claude approved this contract for v1 lock.

Confirmed in review:

- governance boundary is explicit: feedback and telemetry cannot trigger live mutation or promotion directly
- trader feedback and execution telemetry stay as separate event families with a shared linkage surface
- the linkage object is sufficient for REG-001 and downstream evaluator / optimizer work

Tracked follow-up notes:

- keep `actor_role` aligned with OC-001 governed roles
- keep the contract text explicit that telemetry excludes `draft` execution state
- keep `annotate` documented as a non-destructive custom edit operation
