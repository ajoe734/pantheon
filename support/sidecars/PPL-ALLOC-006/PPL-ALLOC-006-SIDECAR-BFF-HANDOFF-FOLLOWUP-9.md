# PPL-ALLOC-006 BFF / Frontend Handoff Follow-Up 9

Task: `PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-9`  
Parent: `PPL-ALLOC-006`  
Owner: Codex  
Reviewer: Claude  
Kind: support-only `bff_handoff_packet`  
Generated: 2026-07-11

## Boundary

This packet is a reviewer handoff for the remaining absorption gap after
Follow-Up 8. It changes no canonical truth, BFF route or schema, frontend
source, policy, runtime, registry, governance implementation, or parent task
lifecycle. The parent owner decides whether to absorb the referenced frontend
branch and whether residual gaps become new implementation tasks.

## Current Delivery Delta

- `execute-plans` PR `#251`, `task/PPL-ALLOC-006-workbench`, remains open and
  mergeable at head `436aa32eaa24b4f048ae0b08c8a46686ceb56659`.
- Consequently, the richer Promotion & Allocation workbench described by
  Follow-Up 8 remains branch-only. It must not be represented as merged or
  hosted delivery.
- The Pantheon BFF continues to expose distinct promotion review,
  allocation-evaluation, rebalance list/detail, proposal creation, and apply
  surfaces. Their existence does not collapse the operator evidence states.
- No new evidence reviewed here closes authoritative allocation readback after
  apply, or installs a governed emergency-containment mutation in the parent
  workbench.

## Parent Absorption Matrix

| Capability | May be absorbed when | UI claim allowed | Must remain closed when |
|---|---|---|---|
| Workbench shell | PR `#251` is merged from the cited head or a reviewed successor | Primary inspection entry for paper candidates, real ranking, quarterly capital, formula policy, and containment records | The implementation exists only on the open branch |
| Recommendation | The adapter retains the server recommendation, review, inbox, command, and audit identifiers | `recommendation submitted` | A receipt or stable review join is missing; never infer approval |
| Allocation preview | The complete server line set, caps, exclusions, evidence, and snapshot identity survive decoding | `target calculated`, explicitly advisory and `applied: false` | The client has only partial rows or recomputed policy fields |
| Rebalance proposal | A dedicated create response returns a durable `rebalance_id` under one intent-scoped idempotency key | `proposal created` | Only a dry-run id, list row, or incomplete preview exists |
| Apply | Current detail proves simulation, constraints, rollback target, state, and bound approval before command submission | `apply submitted` after the accepted command receipt | Detail is stale/unavailable, approval is absent, or live increase returns `409` |
| Applied allocation | A named authoritative capital/binding query reads back the intended new weights and identities | `applied confirmed` | Only proposal state, elapsed time, toast, or command acceptance is available |
| Emergency containment | The governed helper and PPL-ALLOC-008 authorization plus risk-decreasing negative tests are present | The exact accepted containment state | Only Human Inbox inspection exists; expose no direct mutation fallback |

## Minimal Review Evidence

Claude should request these items from the parent implementation PR before
approving an enabled write journey:

1. The merged `execute-plans` commit containing the workbench, rather than an
   open-branch reference alone.
2. Component or adapter coverage proving independent loading, degraded, stale,
   and error states for ranking, reviews, bindings, rebalance list, and
   rebalance detail.
3. A replay test showing one operator intent maps to one recommendation or
   rebalance resource and restores the original identifiers.
4. A negative test showing list-only or failed/stale detail cannot enable
   apply.
5. A receipt-to-readback test naming the authoritative query used for
   `applied confirmed`; otherwise the journey must stop at `apply submitted`.
6. For emergency controls, PPL-ALLOC-008 evidence that promotion and allocation
   increase are rejected and that reason, evidence, role, and audit metadata
   are mandatory.

## Reviewer Decision

Approve absorption of this packet as a support artifact when the parent keeps
branch delivery, mutation receipts, and authoritative readback distinct.
Request changes if any UI path:

- labels open-branch code as merged or deployed;
- joins by display name, array position, newest record, or matching weight;
- treats recommendation submission as review approval;
- enables apply from list data or an incomplete/stale detail response;
- advances `current_weight` before authoritative readback; or
- invents an emergency REST fallback around the governed action boundary.

## Review And Composition

Owned here: support-only absorption matrix, evidence checklist, and reviewer
decision rules.  
Not changing: L1/L2 truth, BFF/frontend implementation, route contracts,
runtime/registry/governance behavior, dependency ownership, or parent
lifecycle.  
Composes with: parent `PPL-ALLOC-006`, `PPL-ALLOC-003` binding reads,
`PPL-ALLOC-004` allocation semantics, `PPL-ALLOC-008` emergency containment,
and the preceding PPL-ALLOC-006 BFF handoff packets.

## Sources Reviewed

- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-7.md`
- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-8.md`
- `docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap/PPL-ALLOC-006-fe-promotion-allocation-workbench.md`
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/test_bff_promotion_review_governance.py`
- `services/control-plane/bff/test_bff_persona_allocation_policy.py`
- `services/control-plane/bff/test_bff_rebalance_proposals.py`
- `execute-plans` PR `#251` metadata and head `436aa32eaa24b4f048ae0b08c8a46686ceb56659`
