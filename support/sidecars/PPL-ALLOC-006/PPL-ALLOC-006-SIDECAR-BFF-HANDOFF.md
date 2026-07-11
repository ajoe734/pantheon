# PPL-ALLOC-006 BFF / Frontend Handoff Packet

Task: `PPL-ALLOC-006-SIDECAR-BFF-HANDOFF`  
Parent: `PPL-ALLOC-006`  
Owner: Codex  
Reviewer: Claude  
Kind: support-only `bff_handoff_packet`

## Purpose And Boundary

This packet gives the parent frontend owner a composition map for the unified
Promotion & Allocation workbench. It inventories existing BFF surfaces and
identifies UI query gaps; it does not change canonical policy, runtime behavior,
registry/governance truth, BFF implementation, or `execute-plans` source.

The workbench remains a projection over governed records. It must never infer
that a recommendation, submitted review, approval, or accepted command has
already changed live capital.

## Operator Journey

1. **Paper candidates**: inspect stage, eligibility/sample sufficiency, score,
   recommendation, and evidence; submit a recommendation for review.
2. **Human review**: follow the returned review/inbox identifier and render the
   review state separately from the recommendation state.
3. **Real ranking**: inspect canary/live current and calculated target weights,
   delta, exclusions, and every cap reason. Calculation is advisory.
4. **Quarterly capital**: create an auditable rebalance proposal, inspect its
   simulation/constraints/rollback target, then deep-link to the proposal or
   Human Inbox detail.
5. **Approval and apply**: show approval receipt independently. Only a later
   apply command receipt plus refreshed read model may be described as applied.
6. **Emergency actions**: show breach/containment recommendations and route the
   operator to the governed action. Emergency containment may only reduce,
   freeze, suspend, risk-off/rollback, or retire; it cannot promote or increase.

## BFF Composition Matrix

| Workbench need | Existing surface | UI use and truth boundary |
|---|---|---|
| Paper/canary/live ranking | `GET /bff/management/quarterly-ranking` | Ranking snapshot, formula/window/evidence and governance state. Treat as read-only. |
| Stage-aware recommendations | `GET /bff/management/quarterly-ranking/recommendations` | Candidate rows and recommendation identifiers. Do not equate a returned recommendation with a submitted review. |
| Ranking explanation | `GET /bff/management/quarterly-ranking/drilldown` | Persona contribution breakdown for evidence/explanation drawer. |
| Formula policy | `GET /bff/management/quarterly-ranking/formula` | Diagnostics only; not a second action workflow. |
| Submit recommendation | `POST /bff/management/quarterly-ranking/recommendations/{recommendation_id}/submit` | Returns governance intent/command metadata and creates or links a review item; no live-capital mutation. |
| Review queue/detail | `GET /bff/management/promotion-reviews` and `GET /bff/management/promotion-reviews/{review_id}` | Render review stage, requested transition, evidence, capital impact, and decision state. |
| Review decision | `POST /bff/management/promotion-reviews/{review_id}/decisions` | Governed decision receipt. Approval is not rebalance application. |
| Fleet/binding context | `GET /bff/management/persona-fleet` | Join by persona id for stage and binding identity; paper ledger must stay distinct from real pool/sleeve. |
| Target calculation | `POST /bff/management/allocation-policy/evaluate` | Returns lines and `applied: false`; retain cap reasons and exclusions verbatim. |
| Proposal list | `GET /bff/rebalances` | Quarterly-capital table, filterable by status/pool. |
| Proposal create | `POST /bff/rebalances` | Requires idempotency and proposal fields; returns `rebalance_id`. Creation is not approval/application. |
| Proposal detail | `GET /bff/rebalances/{rebalance_id}` | Canonical proposal detail source for simulation, constraints, lines, rollback and approval references. |
| Apply proposal | `POST /bff/rebalances/{rebalance_id}/apply` | Live increases require `approval_ref`; accepted command response is not final execution proof. |
| Emergency guard | governed `EmergencyContainment` operator command validation and emergency proposal validation | UI must send reason/evidence and only risk-decreasing actions; surface 4xx policy failures rather than rewriting them locally. |

All authenticated reads must display degraded/unavailable surface metadata when
present. All writes must preserve the BFF's role, idempotency, confirmation,
and precondition behavior; the frontend must not emulate these gates.

## Required View Model

Keep four identifiers/states separate in row state:

```text
recommendation_id + recommendation_state
review_id         + review_state / decision_state
rebalance_id      + proposal_state / approval_ref
command_id        + command_state / audit receipt
```

Recommended display progression:

```text
recommended -> review submitted -> approved/rejected
            -> proposal created -> apply submitted -> applied (read-model confirmed)
```

`approved` must never render as `applied`. `apply submitted` must remain pending
until a refreshed authoritative read proves the allocation/binding changed.
When writes are disabled, local-only, dry-run, or unavailable, disable the
action and label that condition explicitly; never optimistic-update capital.

Each real-ranking line should preserve, without UI recomputation:

- `persona_id`, `stage`, and `capital_scope`;
- pool or sleeve identity;
- `current_weight`, `target_weight`, and `delta`;
- eligibility/exclusion and `cap_reasons`;
- evidence references and ranking snapshot id;
- linked recommendation, review, and rebalance ids where available.

## Query Gaps And Parent Decisions

These are composition gaps, not authorization for this sidecar to implement a
new BFF contract:

1. There is no single workbench aggregate endpoint. The frontend currently
   must join ranking/recommendations, persona fleet, promotion reviews, and
   rebalances by stable ids. Use independent loading/error states so one
   degraded source does not fabricate a complete row.
2. Emergency containment validation exists, but the parent must confirm the
   installed `execute-plans` command helper/path used by the governed operator
   action catalog. Do not invent a workbench-only REST path.
3. An accepted apply command is not definitive execution completion. Refresh
   rebalance and binding/allocation reads; if no authoritative applied marker is
   available, display `apply submitted` and retain the receipt link.
4. The workbench should prefer server-provided stage, target, delta, caps,
   eligibility, and governance states. Any missing field must render unknown or
   unavailable, not a client-derived policy answer.
5. Legacy page redirects/nav pruning belong to `PPL-ALLOC-007`; this task may
   link to the intended workbench tabs but should not claim that route pruning
   is complete.

## Frontend Handoff Checklist

- Implement the four parent tabs: `paper-candidates`, `real-ranking`,
  `quarterly-capital`, and `emergency-actions`.
- Preserve query parameters for `persona_id`, `review_id`, `rebalance_id`, and
  `capital_id` so legacy deep links can land on the relevant row/detail.
- Fetch recommendation submit/proposal create with idempotency and display the
  returned review/rebalance/command identifiers.
- Link review actions to `/management/human-inbox/:id` and proposal review to
  `/management/rebalance/:id` (or the equivalent tab-preserving redirect).
- Show simulation, constraints, rollback target, evidence, and cap reasons
  before enabling approval/apply affordances.
- Require explicit reason/evidence for emergency actions and never offer an
  emergency promotion or increase control.
- Test that recommendation, review, approved, apply-submitted, and applied are
  visually and semantically distinct.
- Test degraded reads and write-disabled/local-only operation without
  optimistic live-capital changes.

## Composition Notes For Parent Owner

Owned here: support mapping and fail-closed frontend handoff guidance.  
Not changing: canonical policy, BFF routes/contracts, runtime/registry/
governance implementation, frontend code, or route pruning.  
Compose with: `PPL-ALLOC-003` normalized binding reads, `PPL-ALLOC-004`
allocation/rebalance policy, the `PPL-ALLOC-006` execute-plans workbench, and
later `PPL-ALLOC-007`/`PPL-ALLOC-008` route and emergency slices.

## Source Evidence Reviewed

- `docs/04/pantheon_persona_promotion_allocation_gap_2026-07-07/PERSONA_PROMOTION_ALLOCATION_GAP_SPEC.md`
- `docs/04/pantheon_persona_promotion_allocation_gap_2026-07-07/PPL-ALLOC-001-CURRENT-STATE-AUDIT.md`
- `docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap/PPL-ALLOC-003-capital-binding-read-model.md`
- `docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap/PPL-ALLOC-004-ranking-allocation-policy.md`
- `docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap/PPL-ALLOC-006-fe-promotion-allocation-workbench.md`
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/emergency_containment_policy.py`
