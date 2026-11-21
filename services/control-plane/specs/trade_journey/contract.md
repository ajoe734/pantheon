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
  - `broker_record` — order exists → else `broker_record_missing` (or `unverified` with `missing_evidence: ["broker_order_id"]` when `broker_order_id` was not recorded); `broker_fill_qty`, `broker_fill_price`, `broker_status` (filled), `broker_side`, `broker_instrument_id` equal the fill event → else `broker_fill_mismatch`.
  - `position_change` — `position_quantity_after − position_quantity_before` = signed fill quantity; `committed_snapshot_position` — snapshot `position_qty` = `position_quantity_after` → else `position_variance`.
  - `cash_change` (cash_share only) — `cash_after − cash_before` = `−signed quantity × price × multiplier − fee − tax` (explicit `0.0` `fee` and `tax` are valid numeric inputs, whereas absent or invalid `fee`/`tax` never substitutes `0.0` and leaves `cash_change` `unverified` with `missing_evidence` listing `"fee"` and/or `"tax"`); `committed_snapshot_cash` — snapshot `cash` = `cash_after` → else `cash_variance`.
  - Fee and tax are ledger-owned (the broker paper record carries 0) and are never compared with the broker. A broker read failure defers the journey like an unhealthy runtime and, 60 minutes after the `position_snapshot`, stamps `broker_unavailable_at_reconciliation`. Without a broker URL the `broker_record` check is `not_configured` and position/cash decide.
  - Every numeric comparison check in `reason_detail` carries `check`, `status` (`passed`, `failed`, or `unverified`), `expected`, `actual`, `difference` (`actual − expected`), and `delta` (`actual − expected`). If `expected` or `actual` is missing on a fill journey, that check is marked `status: "unverified"` with `difference: null, delta: null, missing_evidence: [...]`, and if no completed comparison failed the journey is stamped `reconciliation_evidence_incomplete` rather than a false `cash_variance` or `position_variance`.
- `order_rejection` / `order_rejection_simulated` events take `reason_code` as the first non-expression code present of `reject_reason`, `risk_reason`, `reason`, `broker_submission_status`, `reason_code`. Expression choice codes (`long_cash`, `long_lever`, `short_via_future`, `close_leg`) are never a rejection reason and are kept as `expression_reason_code`. `reason_detail` carries the producer-sent `required`, `available`, `limit`, `holding_quantity`, `requested_quantity`. `source_event_id` preserves the canonical telemetry event ID, and `missing_evidence` identifies any omitted producer fields (`reject_reason`, `required`, `available`, `holding_quantity`, `requested_quantity`).

### 4.2.2 Readable BFF Read Fields
The BFF list and detail rows (`/bff/management/trade-journeys`) add these fields on top of the stored journey:
- `persona_name` and `persona_is_probe`: the name from the persona owner record and the `is_probe_or_test_persona` rule. The persona directory is read at most once per 60 seconds per BFF process and shared by list, detail and evidence reads; a failed read keeps the previous directory for at most 300 seconds, after which `persona_name` is null and the row is still returned.
- `include_probe` (query, default false): journeys of probe or test personas are excluded from the list unless `include_probe=true`; `page_info.total` counts the filtered result.
- `attention` (query): `attention` (any of the next three plus `recon_mismatch`), `risk_reject`, `broker_reject`, `partial_fill`, `recon_mismatch`. Applied on the server before pagination; any other value is a 400. `recon_mismatch` excludes rows deferred or unverified for `runtime_unhealthy_at_reconciliation`, `broker_unavailable_at_reconciliation`, `reconciliation_identity_unverified`, or `reconciliation_evidence_incomplete`. Rows of kind `no_trade` are never counted by `attention`.
- `quantity`: the order's requested quantity. `filled_quantity`: the quantity filled (null when nothing filled). Neither is the position after the fill.
- `outcome`: `kind` is `normal`, `no_trade` (a hold or any other no-order decision), `rejected`, `reconciliation_deferred`, `variance` or `open`, with `reason_code`, `reason_detail`, `source_event_id` and (when producer/comparison evidence is missing) `missing_evidence` from the stage event that decided it:
  - **Pre-broker rejection** (`submitted_to_broker: false`, no broker/fill/ledger/reconciliation identifiers): expresses **one cause** at `risk_evaluation` (`rejected_stage: "risk_evaluation"`, `reason_code`: actual rejection reason or `"reason_not_recorded"` when omitted, `reason_detail`: `required`, `available`, `limit`, `requested_quantity`, `holding_quantity` as applicable, `expression_reason_code`: e.g. `"long_cash"` when present, `source_event_id`: canonical rejection event ID) and **order not submitted** (`submitted_to_broker: false`, `order_status: "not_submitted"`). In `detail.stages` and `detail.stage_events`, `risk_evaluation` carries `status: "rejected"` with the rejection cause while `order_submission` carries `status: "not_submitted", order_status: "not_submitted", submitted_to_broker: false` without repeating a second failure. In `/timeline`, the pre-broker rejection event is presented at `stage: "risk_evaluation", stage_status: "rejected", status: "rejected", order_status: "not_submitted", submitted_to_broker: false`.
  - **Reconciliation outcome classification**: every failed/variance reconciliation outcome carries `classification` (`"accounting_variance" | "runtime_health" | "broker_unavailable" | "unverified_evidence"`), `accounting_variance` (`boolean`), `comparison_completed` (`boolean`), `discrepancies` (`list` of failed completed trade-specific checks with `check`, `expected`, `actual`, `delta`, `difference`), `source_event_id`, and `missing_evidence` (when any comparison check lacks inputs or has conflicting evidence, or when `classification == "unverified_evidence"`). `accounting_variance: true` is reported **only** when a trade-specific comparison (`broker_record`, `broker_fill_qty`, `broker_fill_price`, `broker_status`, `broker_side`, `broker_instrument_id`, `position_change`, `committed_snapshot_position`, `cash_change`, `committed_snapshot_cash`) independently completed with both `expected` and `actual` present, a non-zero discrepancy, and no conflicting evidence on that check; any unrelated check that lacks inputs or has conflicting evidence is retained in `missing_evidence` without clearing proven discrepancies. `runtime_unhealthy_at_reconciliation` (`classification: "runtime_health"`) and `broker_unavailable_at_reconciliation` (`classification: "broker_unavailable"`) return `kind: "reconciliation_deferred"` and `flags.mismatch: false`.
- `next_step`: for `rejected` and `variance` rows, `{action_needed, href}` — a link to an open incident that names the binding (`/incidents/{id}`), otherwise to the persona page (`/personas/{persona_id}`). It is a link, not an action; governed actions stay on the actions route.
- `flags.rejected`: true only when `risk_evaluation`, `order_submission` or `broker_acknowledgement` is `rejected` or `failed`. A journey whose only failed stage is `reconciliation` carries `flags.mismatch` (when not `reconciliation_deferred`) and not `rejected`.
- Timeline `source`: the `correlation_envelope.producer` of the stored event (for example `execution.paper_runtime`, `broker.paper_sidecar`, `reconciliation-drift.scheduled`); absent when the stored event has no envelope.
- Detail `diagnostics`: stages 1-6 that were never linked to this journey are reported as one `upstream_lineage_unlinked` item with `attention: false`; it is not an attention flag.

### 4.2.3 Stable Response Examples & Incremental Storage Impact
Full machine-readable response payloads for `tj_79726064-4cc2-5510-8e11-7f5e59ff89bd` (`91d55953-b575-4fe8-8d39-6e0cade58d59`), `tj_57418ba7-ef37-5015-9b5e-b6423872da89` (`48c946c8-39d1-52bd-b1a1-ce72dc7f6e14`), genuine accounting mismatch, upstream omission, and cross-tenant isolation are published in `docs/deployment/evidence/TRADE-JOURNEY-FAILURE-CAUSE-20261121/evidence.json` for `FE-TRADE-JOURNEY-FAILURE-CAUSE-20261121` and `TRADE-JOURNEY-HISTORICAL-CAUSE-REPAIR-20261121`.

- **Pre-broker rejection (`tj_79726064-4cc2-5510-8e11-7f5e59ff89bd`, event `91d55953-b575-4fe8-8d39-6e0cade58d59`)**:
  ```json
  {
    "journey_id": "tj_79726064-4cc2-5510-8e11-7f5e59ff89bd",
    "status": "failed",
    "current_stage": "risk_evaluation",
    "outcome": {
      "kind": "rejected",
      "rejected_stage": "risk_evaluation",
      "reason_code": "insufficient_cash",
      "reason_detail": {
        "required": 100034.3461,
        "available": 100000.0,
        "limit": 100000.0,
        "requested_quantity": 452.0
      },
      "expression_reason_code": "long_cash",
      "submitted_to_broker": false,
      "order_status": "not_submitted",
      "source_event_id": "91d55953-b575-4fe8-8d39-6e0cade58d59"
    },
    "stages": {
      "risk_evaluation": {
        "status": "rejected",
        "reason_code": "insufficient_cash",
        "reason_detail": {
          "required": 100034.3461,
          "available": 100000.0,
          "limit": 100000.0,
          "requested_quantity": 452.0
        },
        "expression_reason_code": "long_cash",
        "source_event_id": "91d55953-b575-4fe8-8d39-6e0cade58d59"
      },
      "order_submission": {
        "status": "not_submitted",
        "order_status": "not_submitted",
        "submitted_to_broker": false
      }
    }
  }
  ```
- **Reconciliation classifications (`tj_57418ba7-ef37-5015-9b5e-b6423872da89`, event `48c946c8-39d1-52bd-b1a1-ce72dc7f6e14`)**:
  - Unverified historical row (missing comparison evidence): `outcome.kind = "variance"`, `outcome.classification = "unverified_evidence"`, `outcome.accounting_variance = false`, `outcome.comparison_completed = false`, `outcome.reason_code = "reason_not_recorded"`, `outcome.missing_evidence = ["reconciliation_reason", "booking_comparison"]`, `outcome.source_event_id = "48c946c8-39d1-52bd-b1a1-ce72dc7f6e14"`.
  - Runtime health / broker unavailability: `outcome.kind = "reconciliation_deferred"`, `outcome.classification = "runtime_health" | "broker_unavailable"`, `outcome.accounting_variance = false`, `outcome.reason_code = "runtime_unhealthy_at_reconciliation" | "broker_unavailable_at_reconciliation"`, `flags.mismatch = false`.
  - Proven accounting mismatch: `outcome.kind = "variance"`, `outcome.classification = "accounting_variance"`, `outcome.accounting_variance = true`, `outcome.comparison_completed = true`, `outcome.reason_code = "cash_variance"`, `outcome.discrepancies = [{"check": "cash_change", "status": "failed", "expected": 217.5331375, "actual": 172.5331375, "difference": -45.0, "delta": -45.0}]`.
- **Incremental storage impact**:
  - No new tables, files, queues, or background scanners are introduced.
  - **Cadence & count at hosted scale**: Persisted failure-cause fields (`source_event_id`, `expression_reason_code`, `reason_detail`, `order_status`, `risk_status`, `risk_gate_evaluated`, `missing_evidence`) are narrowed in `services/trade_journey/lifecycle_projector.py` strictly to `order_rejection`/`order_rejection_simulated` and `reconciliation_failed` stage events (plus `delta` on the 4 numeric booking checks in `reason_detail` on fill-journey `reconciliation_completed` events; `rejection_status` and `broker_submission_status` are excluded from universal `_PASSTHROUGH_FIELDS` and not persisted) inside the existing `trade_journey_projection.journey_stages.contract_fields` JSONB column via idempotent `ON CONFLICT (tenant_id, environment, journey_id, source_event_id, stage_name) DO UPDATE` (0 extra rows added). At hosted dev scale (~50–200 journeys/day across 3 trading personas), at most 1 `order_rejection` or `reconciliation` stage row per journey carries these keys (~50–200 stage rows/day worst-case; 0 bytes added on `signal_generation`, `trade_decision`, `risk_evaluation`, `order_submitted`, `order_accepted`, `paper_fill_simulated`, `position_snapshot`, and no-trade `reconciliation_completed` stage rows).
  - **Size & growth per day**: ~48–64 bytes added to `reason_detail` (`delta` on the 4 numeric booking checks) on a passing fill-journey `reconciliation_completed` stage row, or ~120–240 bytes added on an `order_rejection` or `reconciliation_failed` stage row, yielding ~2.4–48 KB/day (< 1.5 MB/month) of incremental JSONB payload.
  - **Cap & cleanup**: Per-row JSONB additions are bounded to a fixed key set (`reason_detail` holds at most 5 scalar threshold keys on rejection or the fixed 10-check booking/runtime list on reconciliation; `missing_evidence` holds at most 10 field names). In `docs/operations/storage-manifest.json`, `trade_journey_projection.journey_stages` has `bound_status: "unreviewed"`, `cap: "none verified in code"`, and `cleanup: "none found by the static inventory"` (table-level retention remains tracked under RC3 WP3.1; no table-level retention bound is claimed here, and the incremental field budget is recorded under `changed_fields` on the `trade_journey_projection.journey_stages` manifest entry).

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
