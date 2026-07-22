# LOOP-AUTO-DEP-004 - BFF and Frontend Handoff Packet

**Sidecar kind:** `bff_handoff_packet`
**Sidecar task:** `LOOP-AUTO-DEP-004-SIDECAR-BFF-HANDOFF`
**Parent task:** `LOOP-AUTO-DEP-004` - Split promotion and deployment BFF truth by stage
**Parent owner:** `Codex2`
**Parent reviewer:** `Claude`
**Sidecar owner:** `Codex`
**Sidecar reviewer:** `Codex2`
**Prepared:** `2026-06-27`
**Mutates canonical:** `no`
**Status:** Ready for reviewer handoff

> Support artifact only. This packet does not change L1 truth, core
> contracts, deployment runtime, registry, governance, or BFF implementation.
> It packages current BFF/deployment facts and suggested handoff guidance for
> the `LOOP-AUTO-DEP-004` parent owner to decide what to absorb.

---

## 1. Purpose

`LOOP-AUTO-DEP-004` needs the operator-facing BFF to separate promotion and
deployment truth into distinct stages:

1. approval
2. deployment plan
3. deployment saga
4. runtime binding
5. runtime fleet

The main failure mode to avoid is a single green deployment panel hiding that
only one segment is healthy. An operator must be able to tell whether a stuck
promotion is blocked by approval, plan validity, saga retry/DLQ, missing
RuntimeBinding, or runtime-fleet health.

This sidecar is intentionally narrow: it records the current query surface,
the BFF gap matrix, the operator journey, and frontend handoff notes. It does
not implement the parent route changes.

---

## 2. Parent Acceptance Mapping

| Parent acceptance | Current evidence | Handoff implication |
|---|---|---|
| BFF shows approval, plan, saga, binding, and runtime fleet stages separately | Deployment service exposes projection, saga progress, outbox, and replay routes in `services/deployment/service.py:1852`, `services/deployment/service.py:1956`, `services/deployment/service.py:1967`, and `services/deployment/service.py:2021`. BFF read_store already projects saga progress and runtime binding identifiers into deployment plan records in `services/control-plane/bff/read_store.py:10158` and `services/control-plane/bff/read_store.py:10681`. | Parent work can compose from existing data sources. The missing piece is a clear operator BFF projection that names every stage separately. |
| Failure stage is visible to operator | `read_store` can expose `saga_progress_status`, `blocked_reason`, `retry_state`, and `dlq_count` when canonical saga/outbox records exist (`services/control-plane/bff/read_store.py:10197`, `services/control-plane/bff/read_store.py:10770`). | Parent route should surface these as first-class `saga` stage fields, not buried inside a raw plan blob. |
| Panel does not infer runtime health from deployment metadata | `/api/v1/operator/runtime-state` reads runtime bindings and telemetry separately (`services/control-plane/bff/main.py:14487`), while deployment-plan detail is separate (`services/control-plane/bff/main.py:14020`). | Frontend must not treat `plan.status = executed` or `saga.status = completed` as runtime-fleet health. It must read the runtime-fleet stage independently. |

---

## 3. Current Implementation Snapshot

### 3.1 Planning Context

- SA-21 defines `LOOP-AUTO-DEP-004` as the Wave 3 slice whose output is:
  "BFF shows approval, plan, saga, binding, and runtime fleet stages separately"
  (`docs/04/pantheon_sa/SA-21_global_loop_inventory_autopilot_execution_plan.md:244`).
- SA-21 acceptance says the operator must see whether failure is approval,
  plan, saga, binding, or runtime-fleet related
  (`docs/04/pantheon_sa/SA-21_global_loop_inventory_autopilot_execution_plan.md:258`).
- The loop catalog still marks the promotion/deployment desired and actual
  queries as planned, with operator truth projection assigned to
  `LOOP-AUTO-DEP-004` and `LOOP-AUTO-BFF-001`
  (`docs/deployment/loop-catalog.registry.json:871` and
  `docs/deployment/loop-catalog.registry.json:880`).

### 3.2 Deployment Service Facts

| Surface | Current fact | Evidence |
|---|---|---|
| Derived projection | `DeploymentProjectionReadModelService` composes DeploymentPlan, approval decision, latest saga, runtime binding, registry entry, lifecycle state, and summary. | `services/deployment/service.py:505` |
| Projection routes | Deployment service exposes `GET /api/deployment/projections`, `GET /api/deployment/projections/{plan_id}`, and alias `GET /api/deployment/plans/{plan_id}/projection`. | `services/deployment/service.py:1852` |
| Saga dispatch | `POST /api/deployment/plans/{plan_id}/dispatch` creates or replays the saga bootstrap and first outbox event. | `services/deployment/service.py:1899` |
| Saga progress | Service exposes saga progress by saga id and plan id. | `services/deployment/service.py:1956`, `services/deployment/service.py:1967` |
| Retry / DLQ / replay | Service exposes outbox listing, failure recording, replay, and consume routes. | `services/deployment/service.py:2021`, `services/deployment/service.py:2035`, `services/deployment/service.py:2048`, `services/deployment/service.py:2062` |
| Runtime-manager dispatch adapter | Adapter returns structured outcomes: `success`, `retryable_error`, `terminal_error`, with idempotent replay when a binding is already recorded. | `services/deployment/runtime_manager_dispatch_adapter.py:66`, `services/deployment/runtime_manager_dispatch_adapter.py:161` |

### 3.3 BFF Facts

| Surface | Current fact | Evidence |
|---|---|---|
| Deployment plan list | `GET /api/v1/deployment-plans` lists plans filtered by status and capital pool. | `services/control-plane/bff/main.py:13470` |
| Deployment plan detail | `GET /api/v1/deployment-plans/{plan_id}` returns plan plus approval decision when present. | `services/control-plane/bff/main.py:14020` |
| Operator deployment queue | `GET /api/v1/operator/deployment-plans` produces compact list items with `governance_outcome`, target stage, risk level, and degradation meta. | `services/control-plane/bff/main.py:14300` |
| Operator deployment review | `GET /api/v1/operator/deployment-review/{plan_id}` composes plan, approval, capital pool, persona bindings, runtime binding, rollbacks, allowed actions, latest run, and review. | `services/control-plane/bff/main.py:14371` |
| Runtime board | `GET /api/v1/operator/runtime-state` reads runtime bindings and telemetry independently. | `services/control-plane/bff/main.py:14487` |
| Execute-plans compatibility | `/bff/deployments*` and `/bff/runtimes*` expose deployment/runtime compatibility reads. | `services/control-plane/bff/main.py:44628`, `services/control-plane/bff/main.py:44851` |

### 3.4 Existing Test Evidence

| Test | What it proves | Limitation for parent task |
|---|---|---|
| `services/control-plane/bff/test_read_store_deployment.py` | Canonical overlay can project approval, plan, saga progress, runtime binding, DLQ, retry policy, and blocked reason from governance/runtime snapshot files. | It proves read_store projection, not an explicit operator stage rail. |
| `services/control-plane/bff/test_pkt001_deployment_review_console_contract.py` | Operator deployment list and review routes expose degraded/unavailable semantics and action-authority status. | It does not require `saga` or `runtime_fleet` as named stages. |
| `services/control-plane/bff/test_pkt004_deployment_approval_drilldowns_contract.py` | Deployment-plan and approval-decision drilldowns follow the existing contract filters. | It is approval/plan focused. |
| `services/control-plane/bff/test_p0_paper_operating_loop_smoke.py` | A paper deployment plan can produce a RuntimeBinding and BFF runtime-state row. | It proves a happy-path chain, not failure-stage diagnosis. |
| `services/deployment/test_outbox_consumer_worker.py` | Outbox consumer handles pending events, duplicate receipts, retry scheduling, and DLQ counts. | It is worker/service-level proof, not operator BFF projection. |

### 3.5 Evidence Caveat

`docs/deployment/evidence/ep4-governed-paper/20260419T003658Z/` contains useful
shape examples for plan dispatch and completed saga detail. However, its
`summary.json` reports `overall_result = fail` because a later kill-switch
dispatch failed with HTTP 422. Treat this as payload-shape evidence only, not
as `LOOP-AUTO-DEP-004` closure proof.

---

## 4. BFF Query Gap Matrix

| Stage | Current BFF visibility | Gap for `LOOP-AUTO-DEP-004` |
|---|---|---|
| Approval | Plan detail/review includes `approval_decision`; operator list derives `governance_outcome`. | Need a first-class `approval` stage with state, outcome, decision id, missing-source status, and failure reason. |
| Deployment plan | Plan list/detail expose status, current stage, target stage, transition type, artifact, and capital pool. | Need a first-class `plan` stage distinct from approval, including invalid/missing plan state and source status. |
| Deployment saga | `read_store` can attach `deployment_saga_id`, `deployment_saga_status`, `saga_progress_status`, `blocked_reason`, `retry_state`, and `dlq_count` to plan records. | Operator route should expose a named `saga` stage. The frontend should not have to discover saga truth by inspecting raw plan fields. |
| Runtime binding | Operator review can include `runtime_binding`; runtime-binding detail can link back to deployment plan. | Need an explicit `binding` stage that distinguishes `not_created_yet`, `missing_after_saga_completed`, `inactive`, and `active`. |
| Runtime fleet | Runtime board exists separately and reads runtime/telemetry truth. | Need a plan-scoped `runtime_fleet` stage or linked query rule so deployment review cannot infer fleet health from plan/saga metadata. |

---

## 5. Suggested Parent BFF Projection

This is a support recommendation, not canonical contract text. The parent owner
can use this shape directly or adapt it while preserving the stage split.

Recommended route target:

- keep `GET /api/v1/operator/deployment-review/{plan_id}` as the primary
  plan-scoped operator route;
- add a top-level `stage_truth` or equivalent field that is stable enough for
  frontend rendering and tests;
- optionally add compact stage summaries to
  `GET /api/v1/operator/deployment-plans` for queue badges.

Suggested response fragment:

```json
{
  "stage_truth": {
    "approval": {
      "status": "ok | pending | blocked | unavailable",
      "decision_id": "approval-...",
      "outcome": "approved",
      "state": "decided",
      "failure_reason": null,
      "source": "approval_decisions"
    },
    "plan": {
      "status": "ok | blocked | unavailable",
      "plan_id": "plan-...",
      "plan_status": "approved",
      "current_stage": "none",
      "target_stage": "paper",
      "transition_type": "activate",
      "failure_reason": null,
      "source": "deployment_plans"
    },
    "saga": {
      "status": "ok | pending | running | blocked | failed | unavailable",
      "saga_id": "deployment-saga-plan-...",
      "saga_status": "awaiting_binding",
      "progress_status": "blocked",
      "current_step": "binding_requested",
      "blocked_reason": "runtime-manager unavailable",
      "dlq_count": 1,
      "retry_state": []
    },
    "binding": {
      "status": "ok | pending | missing | inactive | unavailable",
      "runtime_binding_id": "rb-...",
      "runtime_id": "runtime-...",
      "deployment_stage": "paper",
      "binding_status": "active",
      "failure_reason": null
    },
    "runtime_fleet": {
      "status": "ok | degraded | stale | missing | unavailable",
      "runtime_count": 1,
      "active_runtime_count": 1,
      "telemetry_status": "ok",
      "worker_status": "ok",
      "failure_reason": null,
      "source": "operator_runtime_state"
    }
  }
}
```

Minimum derivation rules:

- `approval.status = ok` only when the approval decision exists and is approved
  or approved with conditions.
- `plan.status = ok` only when the plan exists and is in an executable or
  terminal-success state expected by the requested view.
- `saga.status = blocked` when `saga_progress_status = blocked` or `dlq_count > 0`.
- `binding.status = missing` when the saga says completed or runtime load was
  requested but no RuntimeBinding can be found.
- `runtime_fleet.status` must be derived from runtime-state/telemetry/worker
  truth, not from deployment plan status.

Do not introduce a new BFF writer for this slice. The BFF should aggregate
stage truth from deployment, governance, runtime-manager, and telemetry sources
or their existing read-store projections.

---

## 6. Operator Journey

1. Operator opens the deployment queue.
   - Query: `GET /api/v1/operator/deployment-plans`.
   - Expected queue behavior: show target stage, approval outcome, and compact
     stage badges when parent adds them.

2. Operator opens a deployment review.
   - Query: `GET /api/v1/operator/deployment-review/{plan_id}`.
   - Expected detail behavior: render the five stages in fixed order:
     approval -> plan -> saga -> binding -> runtime fleet.

3. Operator diagnoses the first non-OK stage.
   - Approval missing/pending/rejected means approval-stage blocker.
   - Plan absent, rejected, failed, or invalid means plan-stage blocker.
   - Saga pending/running is progress; saga blocked/DLQ is saga-stage blocker.
   - Saga completed but RuntimeBinding missing means binding-stage blocker.
   - Binding active but runtime row, heartbeat, worker, or telemetry unhealthy
     means runtime-fleet blocker.

4. Operator takes action only from the stage with authority.
   - Approval CTAs remain governed by approval/allowed-actions surfaces.
   - Saga replay or DLQ action belongs to deployment saga/outbox authority.
   - Runtime pause/restart/retire action belongs to runtime-manager authority.
   - BFF does not collapse these into a single "retry deployment" command.

5. The panel becomes green only when all five stages are OK or intentionally
   terminal-success. A healthy approval or plan cannot mask a blocked saga; a
   completed saga cannot mask an unhealthy runtime fleet.

---

## 7. Frontend Handoff Rules

- Render a stage rail or table in the fixed order:
  approval, plan, saga, binding, runtime fleet.
- Do not infer later-stage health from earlier-stage success.
- Treat `missing`, `pending`, `blocked`, `failed`, `degraded`, `stale`, and
  `unavailable` as distinct visual states.
- Surface `blocked_reason`, `failure_reason`, latest retry status, and DLQ
  count in the stage where they belong.
- Disable CTAs whenever `meta.surfaces.allowedActions.status != ok` or the
  target stage surface reports unavailable.
- Keep raw plan, approval, saga, binding, and runtime detail expandable for
  operator audit, but drive the main UI from the stage projection.
- If `runtime_fleet` is unavailable, do not show an empty fleet as success.
- If the BFF payload lacks a required stage, treat it as a BFF gap rather than
  synthesizing a green state in the browser.

---

## 8. Suggested Parent Verification

Focused parent tests should cover at least these cases:

| Case | Expected stage result |
|---|---|
| Approved plan with no saga yet | approval OK, plan OK, saga pending/missing, binding pending, runtime_fleet missing |
| Saga has dead-lettered outbox | saga blocked with `blocked_reason`, `retry_state`, and `dlq_count` |
| Saga completed but no RuntimeBinding lookup | binding missing, runtime_fleet missing/unavailable |
| RuntimeBinding active but runtime-state/telemetry absent | binding OK, runtime_fleet degraded/unavailable |
| Happy path with active runtime and heartbeat | all stages OK |
| `PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK=false` and no stores | surfaces unavailable, no green stage |

Suggested focused commands for the parent after implementation:

```bash
pytest services/control-plane/bff/test_read_store_deployment.py
pytest services/control-plane/bff/test_pkt001_deployment_review_console_contract.py
pytest services/control-plane/bff/test_p0_paper_operating_loop_smoke.py
```

Add a new parent-owned BFF contract test for the five-stage projection instead
of relying only on the existing route tests.

---

## 9. Non-Goals And Boundaries

- Do not edit L1 policy or canonical loop catalog in this sidecar.
- Do not make BFF the deployment, saga, runtime, registry, or governance write
  owner.
- Do not hide deployment-service retry/DLQ state behind a generic "failed"
  deployment label.
- Do not mark `LOOP-AUTO-DEP-004` complete from seed data, static registry
  metadata, or EP4 shape evidence alone.
- Do not route new current frontend development through Lovable or the legacy
  `front-ai-trading-system` checkout.

---

## 10. Reviewer Checklist

| Check | Status |
|---|---|
| Support artifact only | PASS |
| Canonical truth untouched | PASS |
| BFF query gaps identified | PASS |
| Operator journey included | PASS |
| Frontend handoff included | PASS |
| Parent acceptance mapped | PASS |
| No runtime/registry/governance implementation changed | PASS |

Suggested review command:

```bash
AI_NAME=Codex2 REVIEW_FILE=support/sidecars/LOOP-AUTO-DEP-004/LOOP-AUTO-DEP-004-SIDECAR-BFF-HANDOFF.md \
  REVIEW_NOTES_ZH="Support-only BFF/frontend handoff packet approved: captures current deployment/BFF evidence, stage-split query gaps, operator journey, frontend rules, and parent verification guidance without canonical truth or runtime changes." \
  ./scripts/ai-status.sh approve LOOP-AUTO-DEP-004-SIDECAR-BFF-HANDOFF \
  "Support-only BFF/frontend handoff packet approved for parent owner absorption."
```

If factual correction is needed:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh reopen LOOP-AUTO-DEP-004-SIDECAR-BFF-HANDOFF \
  "Describe the missing source, incorrect stage mapping, or scope violation."
```

---

## 11. Handoff Status

Prepared by Codex for Codex2 review. Parent owner Codex2 can use this packet as
a support-only starting point for `LOOP-AUTO-DEP-004`; parent absorption remains
the parent owner's implementation decision.
