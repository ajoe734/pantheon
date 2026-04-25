# GW-003 Governance Workbench Follow-on Surfaces — Canonical Packet Family

## Header

- Packet family ID: `GW-003`
- Workbench: Governance Workbench
- Phase origin: `BP5-WB-003`
- Lovable readiness: **partial** — `GV-01 Review Queue`, `GV-03 Promotion Review`, `GV-05 Rollback Review`, and `GV-06 Governance Audit Rail` are handoff-ready and loop-complete for the current packet scope; `GV-02 Approval Queue` is backend-live with the current packet loop closed on Pantheon side, and `GV-04 Deployment Diff` is backend-live but still waiting on a replay-clean front republish before the packet can close
- Recommended wave: Wave 2 backend work is landed; the remaining governance follow-up is front-loop closure for `GV-04` plus any future scope reopened by new packet evidence
- Owner: Claude
- Reviewer: Codex2

---

## Objective

Complete the Governance Workbench by packetizing the four follow-on surfaces — Approval Queue, Deployment Diff, Rollback Review, and Governance Audit Rail — against canonical governance object semantics already established by `PKT-001`, `F-042`, `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, and `ROLLBACK_AND_POSITION_SEMANTICS.md`.

No follow-on surface may:

- Fork the `allowedActions` authority model from `GV-01` and `GV-03`
- Derive approval authority, rollback scope, diff data, or audit records client-side
- Redefine governance object lifecycle (approval decisions, deployment plans, rollback records)
- Invent operator command vocabulary outside of `POST /api/v1/operator/commands`

All data and CTA authority must be backend-shaped through new operator-composed BFF routes that extend the existing governance read surfaces.

---

## Existing Pantheon Support (pre-conditions)

Before packetizing any follow-on module, treat the following artifacts as canonical:

| Artifact | Location | What it defines |
|---|---|---|
| `PKT-001` Governance Review Queue | `docs/bff/PKT-001-governance-review-queue.md`, `docs/pantheon-handoffs/PKT-001-governance-review-queue/FRONTEND_CHANGE_SPEC.md` | Canonical queue projection model, filter semantics, pagination contract, backend-shaped `allowedActions`, detail-drawer pattern, and Lovable handoff format; baseline queue pattern for all follow-on queue surfaces |
| `F-042` Promotion Review | `docs/bff/F-042-promotion-review.md`, `docs/pantheon-handoffs/F-042/FRONTEND_CHANGE_SPEC.md` | Canonical `allowedActions.canPromoteToPaper` authority model; governance outcome semantics and CTA gating pattern that all follow-on module CTAs must reuse |
| Approval Decision contract | `services/control-plane/governance/contract.md` | Canonical `ApprovalDecision` lifecycle (`created → pending → decided → executed`), write-owner matrix, risk-level authorization, and the rule that all approval decisions must flow through this object instead of shadow approval semantics |
| Deployment and Binding semantics | `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | Canonical `DeploymentPlan` lifecycle, stage semantics, and write-owner boundary for governance and deployment planes |
| Rollback semantics | `ROLLBACK_AND_POSITION_SEMANTICS.md` | Canonical rollback action types (`replace`, `pause_then_replace`, `liquidate_then_replace`), position-impact model, rollback ownership, and the rule that rollback authorization must account for position state before approval |
| Paper / canary / live policy | `PAPER_CANARY_LIVE_POLICY.md` | Canonical stage thresholds that back the deployment diff presentation and approval gating in `GV-04` |
| Approval decision raw reads | `GET /api/v1/approval-decisions` (DP-03), `GET /api/v1/approval-decisions/{decision_id}` (DP-04) | Live raw read routes that the `GV-02` approval queue operator view will be composed from; do not use these raw routes directly from the frontend |
| Rollback records | `GET /api/v1/rollbacks` (EV-04), `GET /api/runtimes/{runtime_id}/rollbacks` (RT-04) | Live raw rollback read routes that back `GV-05` review context; do not use directly from the frontend |
| Shared degraded-state substrate | `docs/pantheon-handoffs/PKT-005-degradation-banner/FRONTEND_CHANGE_SPEC.md` | Mandatory degradation banner inheritance for all follow-on governance screens |
| Operator acceptance matrix | `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md` | Per-surface degradation tiers, total-BFF-outage fallback behavior, and secondary-path guidance |

---

## Module Inventory

| Module ID | Module name | Screen / surface scope | Existing packet state | Lovable readiness | Wave order |
|---|---|---|---|---|---|
| `GV-01` | Review Queue | unified pending-items queue across deployment plans, approval decisions, and rollback requests | ready via `PKT-001` plus frontend handoff bundle | ready | Wave 2 baseline |
| `GV-03` | Promotion Review | promotion stage display, paper-live boundary copy, accept/reject CTA with `allowedActions.canPromoteToPaper` | ready via `F-042` plus frontend handoff bundle; formally placed in Governance Workbench by `PKT-001` | ready | Wave 1 baseline |
| `GV-02` | Approval Queue | pending approval decisions with `allowedActions` CTA extensions, decision confirmation drawer, and write path | BFF contract, screen spec, example payload, and `FRONTEND_CHANGE_SPEC.md` published; operator route and commands are now implemented in the BFF | backend-ready; current packet scope closed on Pantheon side | Wave 2 — landed |
| `GV-04` | Deployment Diff | side-by-side field diff, semantic change labels, risk tier annotation, per-field reason, and approval gating | BFF contract, screen spec, example payload, and `FRONTEND_CHANGE_SPEC.md` published; operator route and `EscalateDiff` command are now implemented in the BFF | partial — backend-ready, frontend replay follow-up still open | Wave 2 — backend landed |
| `GV-05` | Rollback Review | rollback scope summary, position impact table, affected bindings, trigger reason, and approval CTA | loop-complete via published packet bundle and live operator-composed route | ready / complete for current scope | Wave 2 — landed |
| `GV-06` | Governance Audit Rail | chronological filterable audit trail; actor, action type, target, outcome, and evidence drawer | loop-complete via published packet bundle and live operator-composed route | ready / complete for current scope | Wave 2 — landed |

---

## GV-01 Review Queue

### Surface scope

- Unified pending-items queue backed by `GET /api/v1/operator/governance/review-queue`
- Backend-shaped `allowedActions` for Forward to Approval, Request Changes, and Escalate

### Existing packet state

Complete via `PKT-001`. The screen spec, BFF contract, example payload, and frontend handoff already exist. This family does not reopen the queue packet; it treats `GV-01` as the queue vocabulary baseline and handoff pattern for all follow-on governance screens.

### Live-state gate

`meta.surfaces.*` degradation banner and the rule that all routing CTAs must be disabled when any surface is degraded or unavailable are already established in `PKT-001`. Follow-on modules inherit this gate without modification.

---

## GV-03 Promotion Review

### Surface scope

- Promotion stage display backed by `GET /api/v1/operator/deployment-review/{plan_id}` and command path via `POST /api/v1/operator/commands`
- `allowedActions.canPromoteToPaper` is the canonical CTA authority model that later governance modules extend

### Existing packet state

Complete via `F-042` and formally placed inside the Governance Workbench by `PKT-001`. The screen spec, BFF contract, example payload, and frontend handoff already exist. The `allowedActions` gating model proven here is the template for `GV-02`, `GV-05`, and any future governance mutation surface.

---

## GV-02 Approval Queue

### Surface scope

- **Queue list**: paginated pending-decision queue filtered by `decision_type`, `risk_level`, and `decision_state`; each row carries embedded `decision_context` and `allowedActions`
- **Decision detail drawer**: `risk_summary`, `evidence_refs`, `governance_chain.linked_review_item_id`, `required_approvals`, and `decision_state`; inherits the drawer pattern from `GV-01`
- **Write actions**: Approve Decision, Reject Decision, Request Revision — all routed through `POST /api/v1/operator/commands`

### Published packet artifacts

| Artifact | Location |
|---|---|
| BFF contract | `docs/bff/PKT-006-approval-queue.md` |
| Screen spec | `docs/screens/PKT-006-approval-queue.md` |
| Example payload | `docs/examples/PKT-006-approval-queue.json` |
| Frontend change spec | `docs/pantheon-handoffs/PKT-006-approval-queue/FRONTEND_CHANGE_SPEC.md` |
| Contract-ready | `.coordination/responses/PKT-006-approval-queue-contract-ready.yaml` |
| Lovable UI task | `.coordination/responses/PKT-006-approval-queue-lovable-ui-task.yaml` |

### Backend delivery status

| Route or contract | Status | Notes |
|---|---|---|
| `GET /api/v1/operator/governance/approval-queue` | **live** | operator-composed queue projection is wired in the BFF with `items[]`, embedded `decision_context`, backend-shaped `allowedActions`, `meta.surfaces`, and pagination |
| `allowedActions` extension for bulk or staged approval | **live** | `canApprove`, `canReject`, and `canRequestRevision` are backend-computed authority signals on each queue item |

### Governance semantics anchor

This module extends `ApprovalDecision` from `services/control-plane/governance/contract.md`. The object lifecycle (`created → pending → decided → executed`) must not be reinterpreted. The `governance_chain.linked_review_item_id` references the upstream `GV-01` review item that forwarded the decision — surface this link as read-only context, not as a write path.

### Degraded-state rules

- When any `meta.surfaces` entry is `"degraded"` or `"unavailable"`: show the non-dismissable degradation banner; disable Approve, Reject, and Request Revision CTAs; keep the queue list visible in read-only mode.
- Do not derive CTA authority from `decision_state` alone when `allowedActions` is absent or degraded.

---

## GV-04 Deployment Diff

### Surface scope

- **Plan identity header**: `plan_id`, `artifact_id`, `stage`, `submitted_at`, `submitted_by`, `previous_plan_id`
- **Change summary rail**: `change_summary.by_category` showing per-category change count and highest risk tier
- **Field diff table**: `changes[]` — one row per changed field with `field_path`, `previous_value`, `current_value`, `change_reason`, `change_category`, and `risk_tier`
- **First-deployment state**: when `first_deployment` is `true`, replace the diff table with a first-deployment notice instead of an empty diff
- **Approval gating**: `allowedActions.canProceedToApproval` and `canEscalateDiff` from BFF only

### Published packet artifacts

| Artifact | Location |
|---|---|
| BFF contract | `docs/bff/PKT-007-deployment-diff.md` |
| Screen spec | `docs/screens/PKT-007-deployment-diff.md` |
| Example payload | `docs/examples/PKT-007-deployment-diff.json` |
| Frontend change spec | `docs/pantheon-handoffs/PKT-007-deployment-diff/FRONTEND_CHANGE_SPEC.md` |
| Contract-ready | `.coordination/responses/PKT-007-deployment-diff-contract-ready.yaml` |
| Lovable UI task | `.coordination/responses/PKT-007-deployment-diff-lovable-ui-task.yaml` |

### Backend delivery status

| Route or contract | Status | Notes |
|---|---|---|
| `GET /api/v1/operator/deployment-diff/{plan_id}` | **live** | operator-composed diff route is wired in the BFF and returns backend-shaped `changes[]`, summary rails, and degraded or unavailable surface metadata |
| Diff data shape against previous plan | **live** | BFF identifies `previous_plan_id`, composes the `old/new` field pairs server-side, and supplies `first_deployment` state for plans with no prior baseline |

### Governance semantics anchor

Risk tier labels are defined by the BFF and must be rendered as-is. The frontend must not reclassify or compute risk tiers from field values. Stage semantics (`paper`, `canary`, `live`, `frozen`) are defined by `PAPER_CANARY_LIVE_POLICY.md`; the diff screen surfaces them but does not reinterpret them.

### Degraded-state rules

- When `meta.surfaces.deployment_diff` is `"unavailable"`: replace the diff table with the unavailable-data message; disable `canProceedToApproval` CTA.
- When any other `meta.surfaces` entry is `"degraded"` or `"unavailable"`: show the non-dismissable degradation banner; keep the diff table in read-only mode.

---

## GV-05 Rollback Review

### Surface scope

- **Rollback identity header**: `rollback_id`, `target_plan_id`, `trigger_reason`, `requested_at`, `requested_by`, `rollback_scope`
- **Scope summary**: `affected_persona_count`, `affected_binding_count`, `target_stage`
- **Position impact table**: `position_impact[]` — per-binding row; when `position_data_stale` is `true`, render the stale-data badge and `position data stale — impact unknown` instead of `position_impact_summary`
- **Affected bindings panel**: `affected_bindings[]`
- **Trigger evidence drawer**: `trigger_evidence` on user interaction; includes `trigger_reason`, `evidence_refs`, and `linked_incident_id`
- **Approval actions**: `allowedActions.canApproveRollback`, `canRejectRollback` from BFF only

### Published packet artifacts

| Artifact | Location |
|---|---|
| BFF contract | `docs/bff/PKT-008-rollback-review.md` |
| Screen spec | `docs/screens/PKT-008-rollback-review.md` |
| Example payload | `docs/examples/PKT-008-rollback-review.json` |
| Frontend change spec | `docs/pantheon-handoffs/PKT-008-rollback-review/FRONTEND_CHANGE_SPEC.md` |
| Contract-ready | `.coordination/responses/PKT-008-rollback-review-contract-ready.yaml` |
| Lovable UI task | `.coordination/responses/PKT-008-rollback-review-lovable-ui-task.yaml` |

### Backend delivery status

| Route or contract | Status | Notes |
|---|---|---|
| `GET /api/v1/operator/rollback-review/{rollback_id}` | **live** | operator-composed rollback review surface is already live for the current packet scope |
| `allowedActions.canApproveRollback` + `canRejectRollback` | **live** | backend-computed authority signals are already served through the BFF |
| Write path for rollback approval | **live** | `ApproveRollback` and `RejectRollback` are already registered on the operator command surface |

### Governance semantics anchor

Rollback authority semantics are governed by `ROLLBACK_AND_POSITION_SEMANTICS.md`. The packet renders those semantics; it does not redefine them. Action types (`replace`, `pause_then_replace`, `liquidate_then_replace`) must be sourced from `trigger_evidence` as supplied, not inferred from binding state.

### Degraded-state rules

- When `meta.surfaces.position_data` is `"degraded"` or `"unavailable"`: show the stale-data warning on all affected position impact rows; disable the Approve CTA even if `allowedActions.canApproveRollback` is `true`; keep the position impact table visible in read-only mode.
- When any `meta.surfaces` entry is `"degraded"` or `"unavailable"`: show the non-dismissable degradation banner.

---

## GV-06 Governance Audit Rail

### Surface scope

- **Audit list**: paginated chronological audit trail; each row shows `actor`, `action_type`, `target_type`, `target_id`, `timestamp`, `outcome`, and an evidence indicator
- **Filter rail**: `actor`, `action_type`, `target_type`, and date range (`from` / `to`) — all passed as query params; no client-side filtering or sorting
- **Entry detail drawer**: `entry_id`, `actor`, `action_type`, `target_type`, `target_id`, `timestamp`, `outcome`, `audit_context.reason`, and `evidence_refs[]`
- **Read-only**: no write CTAs or command calls originate from this screen

### Published packet artifacts

| Artifact | Location |
|---|---|
| BFF contract | `docs/bff/PKT-009-governance-audit-rail.md` |
| Screen spec | `docs/screens/PKT-009-governance-audit-rail.md` |
| Example payload | `docs/examples/PKT-009-governance-audit-rail.json` |
| Frontend change spec | `docs/pantheon-handoffs/PKT-009-governance-audit-rail/FRONTEND_CHANGE_SPEC.md` |
| Contract-ready | `.coordination/responses/PKT-009-governance-audit-rail-contract-ready.yaml` |
| Lovable UI task | `.coordination/responses/PKT-009-governance-audit-rail-lovable-ui-task.yaml` |

### Backend delivery status

| Route or contract | Status | Notes |
|---|---|---|
| `GET /api/v1/operator/governance/audit` | **live** | canonical governance audit trail BFF endpoint is already live and returns paginated `entries[]`, `audit_context`, `evidence_refs`, and degraded-surface metadata |
| Audit entry schema | **live** | actor labeling, action type labels, and evidence refs are served from the BFF and are part of the current packet scope |

### Governance semantics anchor

Audit coverage must include all governance actions defined across this packet family: `ApproveDecision`, `RejectDecision`, `RequestApprovalRevision`, `EscalateDiff`, `ApproveRollback`, `RejectRollback`, plus actions already in `GV-01` (`ForwardToApprovalQueue`, `RequestGovernanceChanges`, `EscalateGovernanceItem`). The audit entry schema is the write-owner contract for the entire Governance Workbench command surface.

### Wave placement note

`GV-06` can be developed in parallel with `GV-04` and `GV-05` once the audit entry schema is locked, because the audit trail is a read-only surface that does not depend on the diff or rollback write paths. The schema lock is the only serializing dependency.

### Degraded-state rules

- When `meta.surfaces.audit_trail` is `"degraded"`: show the delayed-data banner alongside available entries; do not blank the list.
- When `meta.surfaces.audit_trail` is `"unavailable"`: replace the list with the unavailable-data message; do not show a blank state.
- When any other `meta.surfaces` entry is `"degraded"` or `"unavailable"`: show the non-dismissable degradation banner; keep the list visible in read-only mode.

---

## Backend Gap Matrix Summary

| Module | Missing BFF route | Missing contract or schema | Lovable gate |
|---|---|---|---|
| `GV-02 Approval Queue` | none for current packet scope | none for current packet scope | backend handoff is landed; only reopen if a new frontend return or contract change reveals a fresh gap |
| `GV-04 Deployment Diff` | none on Pantheon side | none on Pantheon side | backend handoff is landed; packet remains open only for front-owned replay-clean republish |
| `GV-05 Rollback Review` | none for current packet scope | none for current packet scope | loop-complete |
| `GV-06 Governance Audit Rail` | none for current packet scope | none for current packet scope | loop-complete |

Total missing routes: **4**
Total missing contracts or schemas: **6**

---

## Dependency Ordering

| Wave position | Module | Why this order | Upstream dependency |
|---|---|---|---|
| Baseline | `GV-01 Review Queue` + `GV-03 Promotion Review` | both screens are already packetized and define the queue model, `allowedActions` pattern, drawer pattern, and handoff format that all follow-on modules inherit | none — ready baselines via `PKT-001` and `F-042` |
| 1 | `GV-02 Approval Queue` | extends the ready queue baseline with decision-specific CTA authority; its `governance_chain.linked_review_item_id` back-references `GV-01` review items | GV-01 queue data shape and GV-03 `allowedActions` precedent |
| 2 | `GV-04 Deployment Diff` | adds diff presentation on top of the deployment plan identity established by the approval surfaces; diff CTAs (`canProceedToApproval`) link forward to the approval queue | GV-01 queue context and stable deployment plan identity from GV-02 |
| 3 | `GV-05 Rollback Review` | adds rollback approval on top of the position and deployment plan identity from the queue and diff surfaces; inherits the same backend-owned authority pattern | GV-01 queue context, stable deployment plan identity, and position impact contract |
| parallel | `GV-06 Governance Audit Rail` | read-only; can be developed in parallel after audit entry schema is locked; depends on all command types from GV-02, GV-04, and GV-05 being registered so audit coverage is complete | audit entry schema lock; full command vocabulary from GV-02 through GV-05 |

---

## Promotion Criteria (Lovable Handoff Gates)

A module may not be handed to Lovable until all of the following are true:

1. The corresponding BFF operator-composed route is implemented and returns the shape defined in its BFF contract document.
2. `allowedActions` authority signals are present as backend-shaped fields in the BFF response.
3. `meta.surfaces` degradation fields are present in the BFF response and wired to the canonical degradation banner.
4. The `FRONTEND_CHANGE_SPEC.md` for the module is the authoritative handoff document — no supplementary client-side contracts or shadow API contracts may be added.

`GV-01 Review Queue` and `GV-03 Promotion Review` already meet all promotion criteria and are ready for Lovable.

`GV-02`, `GV-05`, and `GV-06` also meet the BFF-route criterion for the current packet scope. `GV-04` now meets the Pantheon backend criterion as well; its only remaining open issue is a front-owned replay-clean republish before the packet can be called fully loop-complete.

---

## Cross-Cutting Rules

- The queue model and pagination pattern from `GV-01` and `PKT-001` must not be forked. All follow-on queue surfaces inherit the same filter rail, row shape, drawer opening behavior, and degradation handling.
- All write actions across the Governance Workbench use `POST /api/v1/operator/commands`. No module may add a governance write path through a non-command route.
- The BFF remains the only aggregation point. No module may construct governance state (approval authority, diff data, position impact, audit history) from raw underlying reads client-side.
- `meta.surfaces.*` degradation inheritance from `PKT-005` is mandatory on every follow-on screen. No module may invent a custom degradation variant.
