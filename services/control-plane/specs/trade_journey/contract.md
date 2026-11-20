# Trade Journey Observability and State Contract

**Task:** TJ-E2E-002
**Owner:** Antigravity
**Reviewer:** Claude
**Status:** DRAFT — ready for orchestration and governance review

---

## 1. Purpose

This contract establishes a formal, machine-readable domain model and transition logic for end-to-end trade observability in the Pantheon platform. It aligns the three lifecycle boundaries:
1. **Research Journey (`ResearchJourney`)** – Tracking research questions to candidates.
2. **Strategy Lifecycle (`StrategyLifecycle`)** – Tracking candidate promotion to deployment.
3. **Trade Journey (`TradeJourney`)** – Tracking signals, execution, and reconciliation.

This contract provides:
- Machine-readable JSON Schemas for validation.
- Clear transition invariants, roll-up rules, and terminal states.
- Exact mapping of parent/child/basket relationships and correction events.
- Audit evidence, RBAC redaction, and data-quality guidelines.

---

## 2. Journey Identity & Cardinalities

### 2.1 The Three Journey Layers

| Identifier | Created At | Primary Owner | Target Domain |
|---|---|---|---|
| `research_journey_id` (`rj_...`) | Hypothesis/rationale formulation | Persona / Research | Research, Alpha Factory |
| `strategy_lifecycle_id` (`sl_...`) | Registry normalisation / promotion | Registry / Promotion | League, Promotion & Allocation |
| `journey_id` (`tj_...`) | Signal generation boundary | Execution (Signal Engine) | Execution, Telemetry, Reconciliation |

### 2.2 Cardinality Rules

```text
Research Journey (1)
  └─ Strategy Candidates (N)
       └─ Strategy Lifecycle (1 per candidate)
            └─ Trade Journeys (N per deployment)
```

- **Research to Strategy**: A single `ResearchJourney` can output 0 to N strategy candidates.
- **Strategy to Trade**: A single deployed `StrategyLifecycle` version generates 0 to N `TradeJourney` instances.
- **Signals to Orders**: One signal generation can result in 1 to N decisions (e.g. split across portfolios). One decision can trigger 1 to N order intents. One order intent can generate 1 to N broker orders due to cancel/replace or venue routing.

---

## 3. Journey Creation & Propagation

### 3.1 Creation Point
- The `journey_id` **MUST** be generated at the **Signal/Decision engine boundary** when a trade intent is formulated.
- Downstream systems (Risk, Execution Router, Broker Adapter, Telemetry Ingestion, Ledger Booking, Reconciliation) **MUST NOT** generate new journey IDs; they must propagate the existing `journey_id` received in the correlation envelope.

### 3.2 Parent/Child/Basket Behavior
- A basket order or batch trade is represented by a parent `TradeJourney` and multiple child `TradeJourney` instances.
- Each child journey has its own `journey_id` and contains a reference `parent_journey_id` pointing to the basket journey ID.
- The parent journey rollup status is derived from the children's rollup statuses (e.g., if one child is `failed`, parent rollup becomes `partially_failed` or `failed` based on risk policies).

---

## 4. Stage & Roll-up Status Machine

### 4.1 Standard Stages (14)

1. `research_rationale`
2. `strategy_candidate`
3. `candidate_evaluation`
4. `promotion_decision`
5. `capital_binding`
6. `deployment_runtime`
7. `signal_generation`
8. `trade_decision`
9. `risk_evaluation`
10. `order_submission`
11. `broker_acknowledgement`
12. `fill_management`
13. `ledger_booking`
14. `reconciliation`

### 4.2 Roll-up Rules
The overall `status` of a `TradeJourney` is calculated deterministically from individual stage statuses:

| Overall Status | Condition |
|---|---|
| `incomplete` | Missing required intermediate stages or events without terminal status, or **conflicting mutually-exclusive terminal states detected** (e.g., failed or cancelled stage exists alongside succeeded execution/reconciliation stages). Raises a data-quality incident. |
| `waiting_human` | Any stage status is `waiting_human`. |
| `blocked` | Any stage status is `blocked`. |
| `executing` | Stage `order_submission` is `succeeded`, but `fill_management` is active. |
| `partially_filled` | Stage `fill_management` is `partially_succeeded` and execution timer expired. |
| `completed` | All stages up to `reconciliation` are `succeeded`, or reconciliation was previously `completed_with_variance` but has been closed/resolved by a valid `CorrectionEvent`. |
| `completed_with_variance` | All stages up to `ledger_booking` are `succeeded`, but `reconciliation` status is `failed` (variance detected) and no `CorrectionEvent` has resolved it yet. |
| `failed` | `risk_evaluation` or `order_submission` or `broker_acknowledgement` is `rejected` or `failed`. |
| `cancelled` | Overall workflow received a valid operator or broker cancel command. |

### 4.2.1 Variance, Rejection and Reconciliation Reasons
- `completed_with_variance` means the trade's own reconciliation found a variance (a failed journey-scoped check). Runtime health (`runtime_state`, `runtime_health_summary`, `queue_lag_ms`, `event_delivery_lag_ms`, `actual_metrics_presence`) and strategy performance drift (`drift_checks`) are not journey variance. A runtime-health-only failure defers the journey to the next scheduled tick; once its `position_snapshot` is 60 minutes old it is stamped `reconciliation_failed`. Drift is reported through the drift incident path only.
- Every `reconciliation_failed` event carries `reason_code` and `reason_detail` (a list of `{check, status, detail, observed, threshold}` per non-ok check). Reason codes: `runtime_unhealthy_at_reconciliation`, `reconciliation_identity_unverified` (non-ok `authoritative_actual_identity`), `reconciliation_check_failed` (any other non-ok check).
- **Booking reconciliation** (journeys with a paper fill; the fill and its `position_snapshot` carry `broker_order_id`, `fill_quantity`, `fill_price`, `fee`, `tax`, `position_quantity_before/after` and, for `cash_share`, `cash_before/after`). One broker read per journey (`GET /api/broker/paper/orders/{order_id}` on `PANTHEON_PAPER_BROKER_URL`, bounded timeout); tolerance 1e-6. Checks and reason codes:
  - `broker_record` — order exists → else `broker_record_missing`; `broker_fill_qty`, `broker_fill_price`, `broker_status` (filled), `broker_side`, `broker_instrument_id` equal the fill event → else `broker_fill_mismatch`.
  - `position_change` — `position_quantity_after − position_quantity_before` = signed fill quantity; `committed_snapshot_position` — snapshot `position_qty` = `position_quantity_after` → else `position_variance`.
  - `cash_change` (cash_share only) — `cash_after − cash_before` = `−signed quantity × price × multiplier − fee − tax`; `committed_snapshot_cash` — snapshot `cash` = `cash_after` → else `cash_variance`.
  - Fee and tax are ledger-owned (the broker paper record carries 0) and are never compared with the broker. A broker read failure defers the journey like an unhealthy runtime and, 60 minutes after the `position_snapshot`, stamps `broker_unavailable_at_reconciliation`. Without a broker URL the `broker_record` check is `not_configured` and position/cash decide.
  - `reconciliation_failed` `reason_detail` lists each check with `expected`, `actual`, `difference`; `reconciliation_completed` lists every passed check with its values. Journeys without a fill (rejected, no-order) are reconciled as before.
- `order_rejection` / `order_rejection_simulated` events take `reason_code` as the first present of `reject_reason`, `risk_reason`, `reason`, `broker_submission_status`, `reason_code`. Expression choice codes (`long_cash`, `long_lever`, `short_via_future`, `close_leg`) are never a rejection reason and are kept as `expression_reason_code`. `reason_detail` carries the producer-sent `required`, `available`, `limit`, `holding_quantity`, `requested_quantity`. Previously projected events are not rewritten.

### 4.2.2 Readable BFF Read Fields
The BFF list and detail rows (`/bff/management/trade-journeys`) add these fields on top of the stored journey:
- `persona_name` and `persona_is_probe`: the name from the persona owner record and the `is_probe_or_test_persona` rule. The persona directory is read at most once per 60 seconds per BFF process and shared by list, detail and evidence reads; a failed read keeps the previous directory for at most 300 seconds, after which `persona_name` is null and the row is still returned.
- `include_probe` (query, default false): journeys of probe or test personas are excluded from the list unless `include_probe=true`; `page_info.total` counts the filtered result.
- `attention` (query): `attention` (any of the next three plus `recon_mismatch`), `risk_reject`, `broker_reject`, `partial_fill`, `recon_mismatch`. Applied on the server before pagination; any other value is a 400. `recon_mismatch` returns `completed_with_variance` rows whose reconciliation was not deferred for runtime health. Rows of kind `no_trade` are never counted by `attention`.
- `quantity`: the order's requested quantity. `filled_quantity`: the quantity filled (null when nothing filled). Neither is the position after the fill.
- `outcome`: `kind` is `normal`, `no_trade` (a hold or any other no-order decision), `rejected`, `reconciliation_deferred`, `variance` or `open`, with `reason_code` and `reason_detail` from the stage event that decided it. A rejection decided before reason fields existed (no `reason_code`, or only an expression choice code) carries `reason_code` `reason_not_recorded`.
- `next_step`: for `rejected` and `variance` rows, `{action_needed, href}` — a link to an open incident that names the binding (`/incidents/{id}`), otherwise to the persona page (`/personas/{persona_id}`). It is a link, not an action; governed actions stay on the actions route.
- `flags.rejected`: true only when `risk_evaluation`, `order_submission` or `broker_acknowledgement` is `rejected` or `failed`. A journey whose only failed stage is `reconciliation` carries `flags.mismatch` and not `rejected`.
- Inferred risk stage: an `order_rejection` stopped before the broker (`risk_gate_evaluated` true, `risk_status` `rejected`, or `submitted_to_broker` false with a stored `reason_code` and no broker, fill, ledger or reconciliation identifier) yields a `risk_evaluation` stage with status `rejected` and the event's `reason_code`. The stage is then not listed as missing and the journey is not `partial` for it. The stored rows are not rewritten.
- Timeline `source`: the `correlation_envelope.producer` of the stored event (for example `execution.paper_runtime`, `broker.paper_sidecar`, `reconciliation-drift.scheduled`); absent when the stored event has no envelope.
- Timeline expression-code mapping: a stored `reason_code` of `long_cash`, `long_lever`, `short_via_future` or `close_leg` describes how the order was expressed, not why it was rejected. It is returned as `reason_code` `reason_not_recorded` with the stored value in `expression_reason_code`.
- Detail `diagnostics`: stages 1-6 that were never linked to this journey are reported as one `upstream_lineage_unlinked` item with `attention: false`; it is not an attention flag.

### 4.3 Correction Events & Revision Semantics
- **Correction Event**: A correction event (`CorrectionEvent`) is an explicit record triggered when an operator or authorized service resolves a detected reconciliation variance. It provides the mechanism to close a `completed_with_variance` journey back to `completed`.
- **Late-Arriving Events & Immutable Snapshots**: Late-arriving events **MUST NOT** overwrite historical events or existing snapshots directly. Instead, when a late-arriving event or correction event is processed:
  1. The read model recalculates the journey's snapshot based on the full timeline.
  2. The `revision` of the `TradeJourney` is incremented.
  3. The `revision` of any modified stages (such as `reconciliation`) is incremented.
- **Data-Quality Incidents**: A single journey is forbidden from exhibiting mutually-exclusive terminal indicators (e.g., failing upstream risk validation while simultaneously showing succeeded reconciliation). If such a conflict is detected during rollup computation, the status rolls up to `incomplete` and a data-quality incident is raised.

---

## 5. Correlation & Redaction Contract

### 5.1 Required Correlation Fields
Every message passing through the trading system must include the `CorrelationEnvelope` carrying:
- `journey_id` (required from `signal_generation` onward; absent/omitted for pre-signal stages 1–6), `correlation_id`, `trace_id`
- `tenant_id`, `environment` (`paper`, `broker_sandbox`, `canary`, `live`)
- Upstream identifiers: `research_journey_id`, `strategy_lifecycle_id` (if available)

Pre-signal stages 1–6 (from `research_rationale` through `deployment_runtime`) do not carry a `journey_id` since it has not yet been minted.

### 5.2 Redaction Behavior
Sensitive data (e.g., broker account numbers, sub-venue identifiers, internal risk limit thresholds) must be redacted at the BFF boundary for users without administrative capabilities:
- **Redacted fields** are replaced with a hashed token or masked string (e.g., `* * * * 1234`).
- Capabilities:
  - `account.read` – access to raw broker accounts.
  - `governance.write` – permission to trigger governed actions.

---

## 6. Machine-Readable Schemas

The following JSON Schemas are published under `services/control-plane/specs/trade_journey/`:
- [Correlation Envelope Schema](file:///tmp/pantheon-worker-worktrees/pantheon/tj-e2e-002/services/control-plane/specs/trade_journey/correlation_envelope.schema.json)
- [Research Journey Schema](file:///tmp/pantheon-worker-worktrees/pantheon/tj-e2e-002/services/control-plane/specs/trade_journey/research_journey.schema.json)
- [Strategy Lifecycle Schema](file:///tmp/pantheon-worker-worktrees/pantheon/tj-e2e-002/services/control-plane/specs/trade_journey/strategy_lifecycle.schema.json)
- [Trade Journey Schema](file:///tmp/pantheon-worker-worktrees/pantheon/tj-e2e-002/services/control-plane/specs/trade_journey/trade_journey.schema.json)
