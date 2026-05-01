# P0-REC-001 Acceptance Packet (Sidecar)

**Parent Task**: `P0-REC-001` — Write basic paper ReconciliationRecord
**Parent Owner**: Codex2
**Parent Reviewer**: Codex
**Parent Status**: `in_progress`
**Sidecar Owner**: Claude2
**Sidecar Reviewer**: Codex2
**Helper Kind**: `acceptance_packet`
**Phase**: Pantheon P0 Paper Loop
**Generated**: 2026-05-01T09:00:00Z

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime / registry / governance implementations. It packages the dependency state, existing asset map, and parent acceptance checklist for `P0-REC-001`.

---

## 1. Dependency Map

### 1.1 Formal Parent Dependencies

| Dependency | Task ID | Status | What P0-REC-001 can reuse |
|---|---|---|---|
| Paper operating loop smoke | `P0-LOOP-001` | done | Proven `DeploymentPlan → RuntimeBinding → paper heartbeat → BFF runtime status` flow; `TelemetryIngestService` with schema + binding validation; `RuntimeSummaryProjectionStore`; 29 passing integration tests |

### 1.2 Existing Codebase Assets P0-REC-001 Should Use Instead of Redefining

| Asset | Location | Locked truth |
|---|---|---|
| `ReconciliationDriftStore.put_reconciliation_record()` | `services/reconciliation-drift/store.py` | Already stores `reconciliation_records.json`; `record_id`, `runtime_binding_id`, `capital_pool_id` fields are in-scope |
| `ReconciliationDriftStore.put_alert_handoff()` | `services/reconciliation-drift/store.py` | Alert / incident bridge already exists; `_incident_severity()` maps status levels to severity strings |
| `TelemetryIngestService` | `services/telemetry/ingest_svc.py` | Validates and persists `TelemetryEvent`; `runtime_binding_id`, `artifact_id`, `capital_pool_id`, `deployment_stage` are already validated on ingest |
| `RuntimeSummaryProjectionStore` | `services/telemetry/runtime_summary.py` | Projects heartbeat / pnl telemetry into BFF-readable runtime status; can serve as source for reconciliation trigger |
| `PaperRuntimeService` | `services/execution/lean_runtime/paper_runtime.py` | Emits `deploy_started`, `deploy_completed`, `heartbeat`, `pnl_snapshot`, `paper_fill_simulated`, `bracket_order_logged` |
| SA-17 ReconciliationRecord contract | `docs/04/pantheon_sa/SA-17_telemetry_reconciliation_evolution_gap_analysis.md` §7.2 | Canonical contract shape for `ReconciliationRecord`; `recon_type`, `severity`, `status` vocabularies |

### 1.3 ReconciliationRecord Contract (from SA-17 §7.2)

```json
{
  "record_id": "rec-...",
  "recon_type": "order_fill|position|cash|broker_snapshot",
  "runtime_binding_id": "...",
  "capital_pool_id": "...",
  "expected_ref": "...",
  "actual_ref": "...",
  "delta_summary": {},
  "severity": "none|low|medium|high|critical",
  "status": "open|acknowledged|resolved",
  "generated_at": "RFC3339"
}
```

P0-REC-001 must also link `artifact_id` (not in SA-17 §7.2 but required by the task acceptance line).

**Suggested minimal P0 extension:**

```json
{
  "record_id": "rec-...",
  "recon_type": "pnl_snapshot|order_fill|position",
  "runtime_binding_id": "...",
  "artifact_id": "...",
  "capital_pool_id": "...",
  "deployment_stage": "paper",
  "expected_ref": "...",
  "actual_ref": "...",
  "delta_summary": {},
  "severity": "none|low|medium|high|critical",
  "status": "open|acknowledged|resolved",
  "generated_at": "RFC3339"
}
```

### 1.4 Downstream Consumers of P0-REC-001

| Consumer | Notes |
|---|---|
| `P0-TEL-PROJ-001` (done) | Runtime summary already projects heartbeat; reconciliation layer reads projected pnl/position as `expected_ref` |
| IncidentCase creation | P0-REC-001 scope: threshold breach → `IncidentCase`; postmortem + EvolutionDecision remain downstream |
| EvolutionDecision (proposed-only) | P0-REC-001 may propose but must not approve or execute an `EvolutionDecision` |

### 1.5 Readiness Verdict

**P0-REC-001 is dependency-unblocked.**

P0-LOOP-001 is done; the paper loop produces validated `TelemetryEvent`s with `runtime_binding_id`, `artifact_id`, and `capital_pool_id`. The `reconciliation-drift` service already has a store and alert bridge. P0-REC-001 needs to wire these together: a triggered (or event-driven) reconciliation step that reads paper telemetry, creates a `ReconciliationRecord`, and opens an `IncidentCase` on threshold breach.

---

## 2. Action-Boundary Map

This table separates what is already locked from what `P0-REC-001` itself must add.

| Scope | Locked now | P0-REC-001 must add |
|---|---|---|
| `ReconciliationRecord` shape | SA-17 §7.2 defines the base contract | `artifact_id` link; `deployment_stage: "paper"` assertion; `recon_type` limited to `pnl_snapshot / order_fill / position` for P0 scope |
| Record store | `ReconciliationDriftStore.put_reconciliation_record()` exists | Wire telemetry output (pnl, position, fill events) as input to record creation after a paper run |
| Threshold check | `_incident_severity()` in `reconciliation-drift/main.py` maps status to severity | Define the P0 thresholds (e.g. `pnl < -X%`, `fill rejected`, `heartbeat missing`) that mark a record `severity: high` or `critical` |
| `IncidentCase` on breach | Alert handoff store exists (`put_alert_handoff`) | Create an `IncidentCase`-style record when threshold breach is detected; link back to `record_id` and `runtime_binding_id` |
| `EvolutionDecision` | Contract exists in governance service | P0-REC-001 may create a `proposed` decision only; must not approve or execute |
| Live broker safety | `_BRACKET_EXECUTION_STAGES = {"paper", "sim", ...}` in executor | Reconciliation is paper-only; must not touch live binding or live telemetry path |

### 2.1 Trigger Model

The minimal P0 trigger is: after a paper run cycle (e.g. after `pnl_snapshot` event ingested), call the reconciliation step once. Two acceptable approaches:

1. **Inline in `TelemetryIngestService`**: After a `pnl_snapshot` event passes ingest, call `ReconciliationService.run_paper_reconciliation(event)` synchronously.
2. **Scheduled / explicit**: A separate `POST /api/reconciliation/paper-run` endpoint that reads the latest projected runtime summary and creates a record.

Either approach is acceptable at P0. The task must choose one and document it.

### 2.2 Threshold Policy

At P0 the threshold can be simple and configurable:

- `pnl_threshold_critical`: if `pnl < -N%` of capital_pool_id, severity → `high` → open IncidentCase
- `fill_rejection_threshold`: if `order_rejection` count in run > N, severity → `medium`
- `heartbeat_missing`: if no heartbeat received in last T seconds at reconciliation time, severity → `high`

These do not need to be persisted to canonical policy at P0. They can be environment-variable defaults with a note that they belong in a future `AlertRule` config.

### 2.3 EvolutionDecision Boundary

P0-REC-001 acceptance says "EvolutionDecision remains proposed only."

This means:
- P0-REC-001 may create an `EvolutionDecision` with `status: proposed` linked to the `IncidentCase`
- The decision must not be auto-approved or auto-executed by this task
- The governance review gate remains out of P0 scope

---

## 3. Acceptance Checklist (for Codex parent-reviewer use)

| # | Check | Done when |
|---|---|---|
| A1 | One paper run creates a `ReconciliationRecord` | `record_id`, `runtime_binding_id`, `artifact_id`, `capital_pool_id`, `deployment_stage: "paper"` are all present and non-empty in the created record |
| A2 | Record is persisted via the existing store | `ReconciliationDriftStore.put_reconciliation_record()` is called; the record is readable via `list_reconciliation_records()` |
| A3 | Record uses only paper-stage telemetry | `deployment_stage` in the record is `"paper"`; live binding data is not consulted |
| A4 | Threshold breach opens `IncidentCase` | When pnl or fill thresholds are breached, an `IncidentCase`-compatible record is created and linked to `record_id` and `runtime_binding_id`; severity field is set |
| A5 | Non-breach run does not open `IncidentCase` | Normal paper run with no threshold breach creates a `ReconciliationRecord` with `severity: none` and no `IncidentCase` |
| A6 | `EvolutionDecision` remains `proposed` only | Any `EvolutionDecision` created by this path has `status: proposed`; no approval or execution call is made |
| A7 | Live path is untouched | The live broker path, live binding, and live telemetry ingest are not modified by this task |
| A8 | Reconciliation is triggered by paper events | The trigger is clearly documented: either after pnl_snapshot ingest or via an explicit reconcile endpoint; not triggered on live events |
| A9 | Tests cover at least the happy path and threshold breach | At minimum: one test for `ReconciliationRecord` creation with correct field population; one test for `IncidentCase` creation on breach |
| A10 | No canonical L1 docs modified | SA-17, LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md, and other L1 documents are not changed by this task |

---

## 4. Reviewer Focus Areas

### 4.1 Verify `artifact_id` linkage

SA-17 §7.2 ReconciliationRecord contract does not include `artifact_id`. The P0-REC-001 acceptance line explicitly requires it. Make sure the implementation adds this field and populates it from the `TelemetryEvent.artifact_id` or the binding resolver.

### 4.2 Do not invent a new telemetry store

`services/reconciliation-drift/store.py` already has `put_reconciliation_record()`. Verify the implementation uses this store, not a new JSONL or in-memory ad-hoc store.

### 4.3 IncidentCase must be structured

An `IncidentCase` at P0 does not need to be full postmortem-ready, but it must be a structured dict with:
- `incident_id`
- `runtime_binding_id`
- `capital_pool_id`
- `record_id` (linking back to the `ReconciliationRecord`)
- `severity`
- `status: open`
- `created_at`

An unstructured log line is not sufficient.

### 4.4 Threshold must be deterministic in tests

Tests must not depend on floating-point randomness or timing. Thresholds should be explicitly set in test fixtures so that breach vs. non-breach cases are deterministic.

### 4.5 `EvolutionDecision` proposed-only guard

If the implementation creates an `EvolutionDecision`, verify it has no code path that calls `approve()`, `execute()`, or similar. The proposed-only constraint is a safety boundary.

---

## 5. Suggested Parent Deliverables

If Codex2 wants the shortest path to a reviewable `P0-REC-001`, this sidecar suggests three concrete outputs:

1. **`services/reconciliation-drift/paper_reconciliation.py`** (new or added to `main.py`)
   - `create_paper_reconciliation_record(telemetry_event, binding, thresholds) -> ReconciliationRecord`
   - `check_thresholds(record) -> (breach: bool, severity: str)`
   - `open_incident_case_if_breached(record) -> IncidentCase | None`
   - `propose_evolution_decision_if_incident(incident) -> EvolutionDecision | None`

2. **Tests in `services/reconciliation-drift/tests/test_paper_reconciliation.py`**
   - `test_paper_run_creates_reconciliation_record` — happy path; all required fields present
   - `test_pnl_threshold_breach_opens_incident_case` — breach triggers `IncidentCase`
   - `test_no_breach_no_incident_case` — normal run produces no incident
   - `test_evolution_decision_is_proposed_only` — proposed decision created but not approved

3. **Integration point documentation in a brief note** (one paragraph, can be inside the test file docstring or a `NOTES.md` in `support/sidecars/P0-REC-001/`)
   - Which trigger is used (inline ingest vs. explicit endpoint)
   - Default threshold values and their env variable names

---

## 6. Files Referenced

### Canonical / Contract Sources

- `docs/04/pantheon_sa/SA-17_telemetry_reconciliation_evolution_gap_analysis.md` §7.2, §10, §12, §14
- `ai-status.json` — P0-REC-001 acceptance criteria
- `docs/02-architecture/consensus/sessions/phase6-2026-05-01-pantheon-p0-paper-loop/planning-session.json`

### Existing Implementation Evidence

- `services/reconciliation-drift/store.py` — `ReconciliationDriftStore`
- `services/reconciliation-drift/main.py` — `_incident_severity()`, alert/evaluation routes
- `services/telemetry/ingest_svc.py` — `TelemetryIngestService`
- `services/telemetry/runtime_summary.py` — `RuntimeSummaryProjectionStore`
- `services/execution/lean_runtime/paper_runtime.py` — `PaperRuntimeService`, `RuntimeTelemetryEmitter`

### P0-LOOP-001 Delivery Evidence

- `services/control-plane/bff/test_p0_paper_operating_loop_smoke.py` — 29-test integration smoke (commit `dbee6fe`)
- `.orchestrator/chair-reviews/p0-loop-001-claude-review.md` — reviewer sign-off

### This Sidecar

- `support/sidecars/P0-REC-001/P0-REC-001-SIDECAR-ACCEPTANCE.md`

---

## 7. Handoff to Reviewer (Codex2)

Codex2, this packet is ready for sidecar review and absorption into the parent `P0-REC-001` task.

What it gives you:

1. **Dependency confirmation**: P0-LOOP-001 is done and provides the paper telemetry foundation
2. **Existing asset map**: `reconciliation-drift` service already has the store and alert bridge; no new storage layer needed
3. **ReconciliationRecord shape**: SA-17 §7.2 base contract plus the `artifact_id` and `deployment_stage: "paper"` extensions required by the acceptance line
4. **Acceptance checklist (§3)**: Ten concrete checks for parent review; can be handed directly to Codex as the review frame
5. **Suggested deliverables (§5)**: Three concrete outputs that satisfy the acceptance criteria without broadening scope

Recommended next step for you as sidecar reviewer:

- Verify the dependency map is accurate against the current `ai-status.json`
- Flag any acceptance checklist items that conflict with L1 canonical truth
- Approve this packet and hand back to Claude2 for sidecar closeout
- The parent task owner (Codex2) can then absorb §3 checklist and §5 deliverables directly into `P0-REC-001` implementation work

---

*Generated by Claude2 as a sidecar `acceptance_packet` helper for P0-REC-001. This file is a support artifact and does not modify canonical truth.*
