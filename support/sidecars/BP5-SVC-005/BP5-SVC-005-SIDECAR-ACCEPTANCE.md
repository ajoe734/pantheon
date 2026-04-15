# BP5-SVC-005 Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Helper parent:** `BP5-SVC-005` — Realize the deployment orchestration saga with outbox and inbox consistency
**Prepared by:** Claude (owner: BP5-SVC-005-SIDECAR-ACCEPTANCE)
**Reviewer:** Codex
**Date:** 2026-04-15
**Status:** accepted — Codex approved 2026-04-15; Claude finalizing to `done`

> **Scope constraint:** This packet is a support artifact only. It does not modify any L1 canonical
> truth, contract file, runtime implementation, or registry. All evidence is drawn from the actual
> deliverables in `services/deployment/`. BP5-SVC-005 itself is already `done` (terminal_status:
> done, archived at 2026-04-15T18:41:42Z). This packet is for helper-task closeout only.

---

## 1. Purpose

This packet provides the BP5-SVC-005-SIDECAR-ACCEPTANCE reviewer (Codex) with:

1. A structured **acceptance checklist** mapping each formal criterion to verifiable evidence
2. A **service boundary inventory** summarising what was built
3. A **test coverage summary** for the saga and outbox/inbox paths
4. A **dependency map** showing which downstream tasks are unblocked once BP5-SVC-005 closed

---

## 2. Acceptance Checklist

Formal acceptance criteria from the planning session and task brief:

> AC-1: "deployment writes and event publication use an explicit transactional outbox path"
> AC-2: "cross-service deploy and rollback flows document and test compensation and idempotent replay behavior"

---

### AC-1: Deployment writes and event publication use an explicit transactional outbox path

| Check | Evidence | Status |
|---|---|---|
| Saga bootstrap creates first outbox event atomically | `DeploymentSagaStore.bootstrap_for_plan()` in `services/control-plane/governance/deployment_saga.py`; called from `DeploymentOrchestrationService.dispatch_plan()` in `service.py:368-373`; response includes `deployment_saga.outbox_event` with `sequence_no=1` | ✅ |
| `runtime.binding.requested` outbox event on dispatch | `dispatch` response body: `deployment_saga.outbox_event.event.event_type == "runtime.binding.requested"` — verified in `test_service.py:332` | ✅ |
| `runtime.binding.created` outbox event on binding step | `POST /api/deployment/sagas/{saga_id}/binding-created` → returns `OutboxRecordBody` with `sequence_no=2` — verified in `test_service.py:388-389` | ✅ |
| `runtime.runtime_active` outbox event on runtime step | `POST /api/deployment/sagas/{saga_id}/runtime-active` → returns `OutboxRecordBody` — verified in `test_service.py:391-396` | ✅ |
| `deployment.saga.failed` outbox event on compensation finalize | `POST /api/deployment/sagas/{saga_id}/compensation/finalize` → returns `OutboxRecordBody` with `event_type == "deployment.saga.failed"` — verified in `test_service.py:497-502` | ✅ |
| Outbox is inspectable and filterable | `GET /api/deployment/outbox` with optional `owner_service` and `aggregate_id` query params in `service.py:773-782` | ✅ |
| Inbox receipts track consumption state | `GET /api/deployment/inbox` returns `InboxReceiptBody` list; each receipt carries `status` field (`applied`, `duplicate`, `out_of_order`) — `service.py:800-811` | ✅ |
| Liveness probe | `GET /health` returns `{"status": "ok", "service": "pantheon-deployment"}` in `service.py:814-816` | ✅ |

**AC-1 assessment: MET.** The dispatch path and every saga step emit an outbox event via the canonical `DeploymentSagaStore`. The outbox is inspectable through a dedicated read endpoint and filtered by `owner_service` / `aggregate_id`.

---

### AC-2: Cross-service deploy and rollback flows document and test compensation and idempotent replay behavior

| Check | Evidence | Status |
|---|---|---|
| Failure path returns `CompensationDecision` with rollback action | `POST /api/deployment/sagas/{saga_id}/failure` → returns `CompensationDecisionBody` with `command_type == "request_rollback"` and `runtime_action` taken from the plan's `rollback.action_type` — `service.py:750-757` | ✅ |
| Compensation finalize emits `deployment.saga.failed` outbox event | `POST /api/deployment/sagas/{saga_id}/compensation/finalize` verified in `test_service.py:497-502`; saga status transitions to `"failed"` | ✅ |
| Rollback action sourced from plan (pause_then_replace) | `test_service.py:454-505`: plan created with `rollback_action="pause_then_replace"`; after failure, compensation decision contains `runtime_action == "pause_then_replace"` | ✅ |
| Idempotent dispatch (saga already exists) | `test_service.py:341-365`: second dispatch returns `replayed=True` and outbox remains a single event; no duplicate bootstrap | ✅ |
| Inbox idempotent consume: duplicate | `test_service.py:404-410`: consuming the same `event_id` twice returns `status == "duplicate"` on the second call | ✅ |
| Inbox idempotent consume: out-of-order detection | `test_service.py:412-417`: consuming `seq3` before `seq2` returns `status == "out_of_order"` | ✅ |
| In-order consume after out-of-order: applied | `test_service.py:432-438`: consuming `seq3` after `seq2` returns `status == "applied"` | ✅ |
| Inbox receipts ordered and queryable | `test_service.py:440-451`: receipt list for `consumer_name + aggregate_id` returns correct status sequence: `applied, duplicate, out_of_order, applied, applied` | ✅ |

**AC-2 assessment: MET.** Compensation path is exercised with the plan's rollback action (including `pause_then_replace`). Idempotent dispatch replay and inbox consume semantics (`applied`, `duplicate`, `out_of_order`) are all tested.

---

## 3. Implementation Inventory

### 3a. Delivered files

| File | Role | Key content |
|---|---|---|
| `services/deployment/service.py` | FastAPI application (BP5-SVC-004 + BP5-SVC-005) | 17 routes: plans, validate, list, get, status, dispatch, strategy-read-model, sagas, binding-created, runtime-active, failure, compensation/finalize, outbox, outbox/consume, inbox, health |
| `services/deployment/models.py` | Pydantic wire models | Request/response bodies: `CreateDeploymentPlanRequest`, `DispatchDeploymentPlanRequest`, `DeploymentDispatchResponse`, `DeploymentSagaBody`, `OutboxRecordBody`, `InboxReceiptBody`, `CompensationDecisionBody`, `DeploymentSagaBootstrapBody`, etc. |
| `services/deployment/test_service.py` | Unit tests | 507 lines; covers health, plan CRUD, stage transition validation, dispatch bootstrap, idempotent dispatch, full saga progress with inbox receipts, compensation finalize |
| `services/deployment/smoke_test.py` | HTTP smoke test | Exercises key routes against a live server |
| `services/deployment/__init__.py` | Package marker | — |

### 3b. Canonical platform-layer dependencies

| Platform object | Source path | Used for |
|---|---|---|
| `DeploymentSaga`, `DeploymentSagaStore`, `DeploymentSagaBootstrap`, `InboxReceipt`, `OutboxRecord`, `CompensationDecision` | `services/control-plane/governance/deployment_saga.py` | Saga state machine, outbox write, inbox receipt tracking |
| `DeploymentPlan`, `DeploymentPlanStore`, `StagePlanner`, `PlanStatus`, `DeploymentStage` | `services/control-plane/governance/deployment_plan.py` | Plan creation, stage transition validation, status machine |

The service layer is the HTTP deployment surface only. The canonical domain objects live in `services/control-plane/governance/`.

### 3c. What is NOT in this service

| Concern | Owned by |
|---|---|
| RuntimeBinding creation and write authority | `services/runtime-manager/` (BP5-SVC-007) |
| Capital pool and binding writes | `services/capital/` (BP5-SVC-006) |
| ApprovalDecision governance API | `services/governance/` (BP5-SVC-003) |
| Artifact state and registry reads | `services/registry/` (BP5-SVC-002) |
| Telemetry ingest | `services/telemetry/` (BP5-SVC-009) |

---

## 4. Test Coverage Summary

### Unit tests (`test_service.py`)

| Test | Coverage |
|---|---|
| `test_health` | `GET /health` |
| `test_create_plan_from_snapshots` | create plan, verify storage, transition_type/runtime_action |
| `test_validate_rejects_skipped_stage_transition` | stage skip (paper → live) rejected |
| `test_create_enforces_rollback_linkage` | plan without rollback ref rejected (422) |
| `test_list_and_get` | list plans by strategy_id, get plan by id |
| `test_duplicate_plan_id_rejected` | duplicate plan_id returns 422 |
| `test_status_transition_updates_read_model` | DRAFT → EXECUTING → EXECUTED; strategy read model reflects executed stage |
| `test_invalid_status_transition_rejected` | DRAFT → EXECUTED skip rejected (400) |
| `test_dispatch_bootstraps_saga_and_persists_outbox` | dispatch creates saga + outbox event; verifies `deployment_contract=DEP-001`, `consistency_contract=DEP-002`, `sequence_no=1` |
| `test_dispatch_is_idempotent_for_existing_saga` | second dispatch returns `replayed=True`; outbox unchanged |
| `test_saga_progress_and_inbox_replay_receipts` | full saga: dispatch → binding-created → runtime-active; inbox consume with `applied`, `duplicate`, `out_of_order` cases; in-order consume after out-of-order returns `applied` |
| `test_post_activation_failure_uses_plan_rollback_action_and_finalize` | failure returns `pause_then_replace`; compensation/finalize emits `deployment.saga.failed`; saga status transitions to `failed` |

Total test functions: **12** across 507 lines including fixtures and helpers.

---

## 5. Dependency Map

Tasks with explicit `depends_on: [BP5-SVC-005]` in the planning session:

| Task | Title | Downstream concern |
|---|---|---|
| BP5-SVC-016 | Package the honest service stack into Docker, compose, and smoke topology | `services/deployment/` is part of the compose-critical stack alongside `services/governance/` |
| BP5-WB-005 | Packetize the Research Workbench family | Research packet work depends on the orchestration plane being closed |

Tasks that benefit transitively through saga or deployment plan closure:

| Task | Path |
|---|---|
| BP5-SVC-007 | RuntimeBinding service cites the outbox-event surface (`runtime.binding.requested`) as its signal contract |
| BP5-SVC-008 | Rollback and replace actions receive their plan-sourced `rollback_action` from the same deployment service |
| BP5-SVC-013 | Operational evolution orchestration includes `redeploy` paths that depend on an honest deployment dispatch surface |

---

## 6. Open Questions / Reviewer Notes

| ID | Note | Disposition |
|---|---|---|
| OQ-1 | `services/deployment/` has no `Dockerfile` today. Packaging and compose ownership is already sequenced into `BP5-SVC-016`. | Not a blocker for the service slice. |
| OQ-2 | The service merges BP5-SVC-004 (DeploymentPlan API) and BP5-SVC-005 (saga/outbox/inbox) into one FastAPI app. This is intentional — the two are tightly coupled and share a single store path. | Acceptable. The boundary is clear in the service docstring and title. |
| OQ-3 | `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md` and `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md` are listed as L1 artifacts in the task. This sidecar does not check for drift between the L1 policy text and the service implementation; that is out of scope for a support packet. | Codex can flag any L1 policy drift as a follow-up if found during review. |

---

## 7. Reviewer Disposition and Owner Closeout

This packet was prepared as a parallel support artifact after BP5-SVC-005 reached `done`.

**What this packet is good for:**

1. Preserve a support-only evidence snapshot for BP5-SVC-005-SIDECAR-ACCEPTANCE helper-task closeout
2. Provide Codex with the acceptance checklist and test evidence before reviewing the sidecar
3. Record the dependency map and boundary inventory for downstream owners (BP5-SVC-007, BP5-SVC-016)

**What this packet does NOT do:**

- It does not modify any L1 canonical truth, contract file, runtime implementation, or registry
- It does not represent a second review of the BP5-SVC-005 implementation (already done and archived)
- Acceptance of this sidecar packet is independent from the parent task lifecycle

**Owner closeout note:** Claude can finalize `BP5-SVC-005-SIDECAR-ACCEPTANCE` to `done` after
Codex approves and returns the helper task.

---

*Sidecar prepared by Claude. Helper kind: `acceptance_packet`. Parent task: `BP5-SVC-005`.*
*Hand-off target: Codex (reviewer: BP5-SVC-005-SIDECAR-ACCEPTANCE).*
