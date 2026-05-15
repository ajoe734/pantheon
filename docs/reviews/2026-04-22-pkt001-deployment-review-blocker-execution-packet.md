# 2026-04-22 PKT-001 Deployment Review Blocker Execution Packet

Status: execution-ready follow-up packet
Source: `EXEC-CLOSEOUT-FRONTEND-002`, current BFF runtime audit, and the live `PKT-001` frontend-feedback follow-up record
Prepared by: Codex

## Purpose

This packet materializes the remaining Pantheon-owned implementation gap that stayed behind after the frontend closeout sweep.

`EXEC-CLOSEOUT-FRONTEND-002` closed the bookkeeping loop truthfully, but it also confirmed that `PKT-001-deployment-review` is still blocked by a real BFF / contract-alignment gap, not by stale closeout text.

## Confirmed Remaining Blocker

- The canonical `PKT-001` contract publishes `GET /api/v1/operator/deployment-plans`.
- The current BFF exposes `GET /api/v1/deployment-plans` and `GET /api/v1/deployment-plans/{plan_id}`, plus `GET /api/v1/operator/deployment-review/{plan_id}`, but the operator-scoped list route still returns `404`.
- The frontend-feedback loop also requires Pantheon to record a truthful decision on whether `/api/v1/runtime/{runtimeId}/events/stream` belongs inside `PKT-001` or remains an approved `PKT-005` substrate cross-cut.

## Pantheon-Owned Scope

- publish `GET /api/v1/operator/deployment-plans` with the page-shaped payload already promised by `docs/bff/PKT-001-deployment-review-console.md`
- align the local contract / closeout / feedback wording so the SSE boundary is recorded truthfully on the Pantheon side
- reduce `PKT-001-deployment-review` from a vague follow-up note to either a closed loop or an explicitly front-owned residual refresh

## Materialized Execution Task

| Task ID | Owner | Reviewer | Depends On | Scope |
|---|---|---|---|---|
| `APP-003-PKT001-BFF-ALIGN-001` | Claude | Codex | - | Publish the missing operator-scoped deployment-plan list route, align `PKT-001` contract/runtime truth, and re-express any remaining front-owned follow-up explicitly. |

## Acceptance Shape

- `GET /api/v1/operator/deployment-plans` no longer returns `404` and matches the published `PKT-001` list contract
- `docs/bff/PKT-001-deployment-review-console.md` and the related closeout / feedback records agree on the SSE boundary decision
- canonical progress truth no longer leaves `PKT-001-deployment-review` stranded as an unmaterialized follow-up

## Expected Outcome

After this packet is executed:

- the remaining `PKT-001` blocker becomes a normal supervisor-visible execution item
- the operator deployment-review surface matches the published contract more closely
- any residual follow-up is narrowed to an explicit external ownership boundary instead of a mixed contract/runtime ambiguity
