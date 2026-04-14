# PKT-001 Governance and Deployment Review Packet Family

## Overview

PKT-001 packetizes the Governance Workbench and Deployment Review Console screen families from existing APP-002 slices. This document is the canonical packet requirements record for these screens.

It reframes `F-042` correctly as **one screen** inside the Governance Workbench — not the whole admin surface — and establishes canonical packet requirements for the remaining governance queue and deployment review screens.

## F-042 Reframe

`F-042 Promotion Review` is a single screen inside the **Governance Workbench**. It is not a full governance or admin implementation.

| Canonical classification | Detail |
|---|---|
| Workbench | Governance Workbench |
| Screen | Promotion Review |
| Screen ID | `screen-governance-promotion-review` |
| Packet status | Done — `contract-ready` and `lovable-ui-task` already published |
| Coordination artifacts | `.coordination/responses/F-042-contract-ready.yaml`, `.coordination/responses/F-042-lovable-ui-task.yaml` |
| BFF contract | `docs/bff/F-042-promotion-review.md` |
| Screen spec | `docs/screens/F-042-promotion-review.md` |
| Example payload | `docs/examples/F-042-review-page.json` |

Existing F-042 coordination artifacts are retained as-is. Their scope is now formally bounded to this single screen.

---

## Screen Inventory

### Operator Console — Deployment Review Console

| Attribute | Value |
|---|---|
| Workbench | Operator Console |
| Screen | Deployment Review Console |
| Screen ID | `screen-operator-deployment-review` |
| Feature ID | `PKT-001-deployment-review` |
| Packet status | **ready** |
| BFF backing | `GET /api/v1/operator/deployment-plans` (list), `GET /api/v1/operator/deployment-review/{plan_id}` (detail), `POST /api/v1/operator/commands` (actions) |
| Lovable readiness | Ready — APP-002 sidecar defines deployment review semantics |
| Screen spec | `docs/screens/PKT-001-deployment-review-console.md` |
| BFF contract | `docs/bff/PKT-001-deployment-review-console.md` |
| Example payload | `docs/examples/PKT-001-deployment-review-console.json` |
| Contract-ready | `.coordination/responses/PKT-001-deployment-review-contract-ready.yaml` |
| Lovable UI task | `.coordination/responses/PKT-001-deployment-review-lovable-ui-task.yaml` |
| BFF-gap template | `.coordination/requests/PKT-001-deployment-review-bff-gap.example.yaml` |
| UI-done template | `.coordination/requests/PKT-001-deployment-review-ui-done.example.yaml` |

### Governance Workbench — Review Queue

| Attribute | Value |
|---|---|
| Workbench | Governance Workbench |
| Screen | Governance Review Queue |
| Screen ID | `screen-governance-review-queue` |
| Feature ID | `PKT-001-governance-review-queue` |
| Packet status | **ready** |
| BFF backing | `GET /api/v1/operator/governance/review-queue` |
| Lovable readiness | Ready — queue read model backed by APP-002 governance operator path semantics |
| Screen spec | `docs/screens/PKT-001-governance-review-queue.md` |
| BFF contract | `docs/bff/PKT-001-governance-review-queue.md` |
| Example payload | `docs/examples/PKT-001-governance-review-queue.json` |
| Contract-ready | `.coordination/responses/PKT-001-governance-review-queue-contract-ready.yaml` |
| Lovable UI task | `.coordination/responses/PKT-001-governance-review-queue-lovable-ui-task.yaml` |
| BFF-gap template | `.coordination/requests/PKT-001-governance-review-queue-bff-gap.example.yaml` |
| UI-done template | `.coordination/requests/PKT-001-governance-review-queue-ui-done.example.yaml` |

### Governance Workbench — Approval Queue

| Attribute | Value |
|---|---|
| Workbench | Governance Workbench |
| Screen | Governance Approval Queue |
| Screen ID | `screen-governance-approval-queue` |
| Feature ID | `PKT-001-governance-approval-queue` |
| Packet status | **blocked** |
| BFF backing | Missing — needs `GET /api/v1/operator/governance/approval-queue` |
| Lovable readiness | Not ready |
| Screen spec | Not yet created |
| Example payload | Not yet created |

**BFF gap:** No approval queue read model exists in the current BFF. Required before this screen can be packetized:
- `GET /api/v1/operator/governance/approval-queue` returning items with `pending_approval_id`, `approval_type`, `risk_level`, `operator_authority`, `allowedActions.canApprove`, `allowedActions.canReject`, and `meta.snapshot_at`.
- `POST /api/v1/operator/commands` with `ApproveGovernanceItem` command and `target.type: ApprovalQueueItem`.

### Governance Workbench — Deployment Diff

| Attribute | Value |
|---|---|
| Workbench | Governance Workbench |
| Screen | Deployment Diff |
| Screen ID | `screen-governance-deployment-diff` |
| Feature ID | `PKT-001-governance-deployment-diff` |
| Packet status | **blocked** |
| BFF backing | Missing — needs `GET /api/v1/operator/deployment-review/{plan_id}/diff` |
| Lovable readiness | Not ready |
| Screen spec | Not yet created |
| Example payload | Not yet created |

**BFF gap:** No deployment diff composed view exists. Required before this screen can be packetized:
- `GET /api/v1/operator/deployment-review/{plan_id}/diff` returning `before_snapshot`, `after_snapshot`, `parameter_delta`, `binding_delta`, `capital_pool_delta`, and `meta.diff_computed_at`.
- Diff surface must be a composed BFF response — the UI must not compute the diff client-side.

### Governance Workbench — Rollback Review

| Attribute | Value |
|---|---|
| Workbench | Governance Workbench |
| Screen | Rollback Review |
| Screen ID | `screen-governance-rollback-review` |
| Feature ID | `PKT-001-governance-rollback-review` |
| Packet status | **blocked** |
| BFF backing | Missing — needs `GET /api/v1/operator/governance/rollback-queue` and `POST /api/v1/operator/commands` with `RequestRollback` command |
| Lovable readiness | Not ready |
| Screen spec | Not yet created |
| Example payload | Not yet created |

**BFF gap:** No rollback review read surface exists. Required before this screen can be packetized:
- `GET /api/v1/operator/governance/rollback-queue` returning pending rollback requests with `rollback_id`, `target_deployment_plan_id`, `requested_by`, `reason`, `allowedActions.canApproveRollback`, and `meta.snapshot_at`.
- Rollback authority must be backend-shaped; the UI must not compute rollback eligibility.

### Governance Workbench — Governance Audit Rail

| Attribute | Value |
|---|---|
| Workbench | Governance Workbench |
| Screen | Governance Audit Rail |
| Screen ID | `screen-governance-audit-rail` |
| Feature ID | `PKT-001-governance-audit-rail` |
| Packet status | **blocked** |
| BFF backing | Missing — needs `GET /api/v1/operator/governance/audit` |
| Lovable readiness | Not ready |
| Screen spec | Not yet created |
| Example payload | Not yet created |

**BFF gap:** No governance audit read model exists. Required before this screen can be packetized:
- `GET /api/v1/operator/governance/audit` returning `events[]` with `event_id`, `event_type`, `actor`, `target_id`, `target_type`, `outcome`, `timestamp`, and `meta.page_token`.
- Audit rail must be read-only; all action authority comes from the queue screens.

---

## Example Payload Gap Summary

| Screen | Example payload status | Gap |
|---|---|---|
| Promotion Review (F-042) | Done | None — `docs/examples/F-042-review-page.json` |
| Deployment Review Console | Done | None — `docs/examples/PKT-001-deployment-review-console.json` |
| Governance Review Queue | Done | None — `docs/examples/PKT-001-governance-review-queue.json` |
| Governance Approval Queue | Missing | Needs approval queue read model BFF route first |
| Deployment Diff | Missing | Needs deployment diff composed view BFF route first |
| Rollback Review | Missing | Needs rollback queue read surface BFF route first |
| Governance Audit Rail | Missing | Needs governance audit read model BFF route first |

---

## Screen-Spec Gap Summary

| Screen | Screen spec status | Gap |
|---|---|---|
| Promotion Review (F-042) | Done | None — `docs/screens/F-042-promotion-review.md` |
| Deployment Review Console | Done | None — `docs/screens/PKT-001-deployment-review-console.md` |
| Governance Review Queue | Done | None — `docs/screens/PKT-001-governance-review-queue.md` |
| Governance Approval Queue | Missing | Blocked on BFF route |
| Deployment Diff | Missing | Blocked on BFF diff endpoint |
| Rollback Review | Missing | Blocked on BFF rollback queue |
| Governance Audit Rail | Missing | Blocked on BFF audit read model |

---

## Lovable Readiness Matrix

| Screen | Lovable readiness | Blocker |
|---|---|---|
| Promotion Review (F-042) | Ready | None |
| Deployment Review Console | Ready | None |
| Governance Review Queue | Ready | None |
| Governance Approval Queue | Not ready | Missing approval queue read model |
| Deployment Diff | Not ready | Missing deployment diff composed view |
| Rollback Review | Not ready | Missing rollback queue read surface |
| Governance Audit Rail | Not ready | Missing audit read model |

---

## Acceptance Verification

| Acceptance criterion | Status |
|---|---|
| F-042 is reframed as one Governance Workbench screen instead of the whole admin surface | Done — see F-042 Reframe section above |
| Deployment review and governance queue follow-up screens receive canonical packet requirements | Done — Deployment Review Console and Governance Review Queue are packet-ready; blocked screens have explicit requirements |
| Required example payloads and screen-spec gaps are explicitly listed | Done — see Example Payload Gap Summary and Screen-Spec Gap Summary tables |

---

## Wave Assignment

| Screen | Recommended wave |
|---|---|
| Promotion Review (F-042) | Wave 1 (already dispatched) |
| Deployment Review Console | Wave 1 |
| Governance Review Queue | Wave 2 |
| Governance Approval Queue | Wave 2 (after BFF route is ready) |
| Deployment Diff | Wave 2 (after BFF route is ready) |
| Rollback Review | Wave 2 (after BFF route is ready) |
| Governance Audit Rail | Wave 2 (after BFF route is ready) |
