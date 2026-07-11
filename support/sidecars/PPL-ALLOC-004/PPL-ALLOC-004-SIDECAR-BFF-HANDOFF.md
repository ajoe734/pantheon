# PPL-ALLOC-004 BFF / Frontend Handoff Packet

Status: sidecar support packet; parent owner decides adoption
Parent task: `PPL-ALLOC-004`
Sidecar task: `PPL-ALLOC-004-SIDECAR-BFF-HANDOFF`
Owner: Codex2
Reviewer: Codex
Target consumers: PPL-ALLOC-004 BFF owner and PPL-ALLOC-006 frontend owner

## Boundary

This packet records the observed BFF surface, remaining query/contract gaps,
and the operator journey needed by the Promotion & Allocation workbench. It
does not define canonical policy, change runtime behavior, or authorize live
capital mutation. The parent owner remains responsible for the implementation
contract and for resolving any difference between this packet and the
canonical gap spec.

## Observed BFF Surface

The current task branch exposes these relevant routes:

| Purpose | Route | Observed contract |
|---|---|---|
| Evaluate target allocation | `POST /bff/management/allocation-policy/evaluate` | Accepts `ranking_snapshot_id` and `rows`; returns stage-aware `lines` with `applied: false`. Read-role gated and does not mutate bindings. |
| List proposals | `GET /bff/rebalances` | Filters by `status` and `pool_id`; returns paginated rebalance records. |
| Create proposal | `POST /bff/rebalances` | Requires an idempotency header and `capital_pool_id`; proposal form requires ranking snapshot, lines, simulation, constraints, and rollback target. Returns an accepted command plus `rebalance_id`. |
| Proposal detail | `GET /bff/rebalances/{rebalance_id}` | Returns the stored proposal/detail row. |
| Apply proposal | `POST /bff/rebalances/{rebalance_id}/apply` | Separate command boundary. A live increase requires `approval_ref`; it does not make proposal creation equivalent to application. |
| Ranking read | `GET /bff/management/quarterly-ranking` | Existing quarterly ranking aggregate. |
| Recommendation read | `GET /bff/management/quarterly-ranking/recommendations` | Existing governance recommendations with zero claimed live mutation. |
| Capital bindings | `GET /bff/capital-pools` and persona-fleet reads | Existing projection supplies stage/scope, pool or sleeve, current/target weights, and binding state where source data exists. |

The proposal-line validator currently requires:

```text
persona_id, stage, capital_scope, current_weight, target_weight, delta,
cap_reasons, evidence_refs
```

The parent acceptance additionally requires a pool or sleeve identifier. The
frontend must therefore consume `capital_scope_id`, `capital_pool_id`, or
`capital_sleeve_id` only when the parent contract includes it on every proposal
line; it must not reconstruct an identifier from persona or display labels.

## BFF Query And Contract Gaps For Parent Resolution

| Priority | Gap / ambiguity | Required parent decision or output |
|---|---|---|
| Blocker | There is no single observed workbench read that joins ranking, eligibility/exclusions, current binding, proposed target, cap reasons, proposal state, and approval state. | Either add a promotion/allocation aggregate read or publish an explicit composition contract with stable join keys and surface health metadata. |
| Blocker | Proposal validation requires `capital_scope` but not a `pool_id` or `sleeve_id` per line. | Require and persist an unambiguous `capital_scope_id` (plus typed pool/sleeve fields if retained) for every line. |
| Blocker | `POST /bff/rebalances` returns command metadata and `rebalance_id`, but the handoff does not establish a stable proposal status enum or approval-state projection. | Publish states that distinguish recommendation, review requested, approved, applied, rejected/expired, failed, and rolled back. |
| Blocker | Apply checks only that a live increase has a non-empty `approval_ref`; the observed surface does not prove that the referenced approval is valid, current, scoped to this proposal, and authorized. | Parent must bind apply to a verified approval record and document mismatch/expired/rejected error behavior. |
| High | The evaluate route accepts caller-supplied rows. The observed contract does not say which server snapshot/binding facts are authoritative or how stale inputs are rejected. | Bind evaluation to a server-owned ranking snapshot, or return provenance and staleness fields sufficient to prove the evaluated universe. |
| High | Proposal list filters do not expose quarter/ranking snapshot/persona/sleeve/approval filters needed by the workbench. | Add filters or an aggregate query so tabs do not fetch all proposals and filter client-side. |
| High | The proposal record stores simulation, constraints, rollback target, approval/audit refs, but their stable frontend shapes are not declared here. | Freeze a response example/schema, including empty/degraded/error behavior and whether refs are strings or typed objects. |
| High | Eligibility and exclusion reasons must be shown independently from cap reasons. | Return `eligible`, stable `exclusion_reasons[]`, and `cap_reasons[]`; never collapse exclusion into a zero target without explanation. |
| Medium | `stage` vocabulary can appear as `paper`, `canary`, `live` in sources and `*_running` in workflow policy. | Normalize the public enum and specify display mapping; frontend should not infer promotion eligibility from string prefixes. |
| Medium | List/detail freshness and partial-source health are not yet expressed as a workbench-level contract. | Return snapshot timestamp, source surface health/degraded reasons, and correlation id for operator troubleshooting. |

## Recommended Read Model For Frontend Composition

This is a handoff shape, not canonical truth. The parent owner may expose it as
one aggregate or guarantee the same fields across composed reads.

```json
{
  "ranking_snapshot_id": "ranking-2026-q3-001",
  "quarter": "2026-Q3",
  "generated_at": "RFC3339",
  "lines": [
    {
      "persona_id": "persona-001",
      "stage": "live_running",
      "capital_scope": "live_sleeve",
      "capital_scope_id": "sleeve-001",
      "capital_pool_id": "pool-001",
      "capital_sleeve_id": "sleeve-001",
      "eligible": true,
      "exclusion_reasons": [],
      "current_weight": 0.10,
      "target_weight": 0.125,
      "delta": 0.025,
      "cap_reasons": ["quarterly_increase_cap"],
      "evidence_refs": ["evidence://ranking/001"],
      "recommendation": "capital_increase_review"
    }
  ],
  "proposal": {
    "rebalance_id": "rb-001",
    "status": "awaiting_approval",
    "approval_ref": null,
    "applied": false
  },
  "meta": {
    "snapshot_at": "RFC3339",
    "correlation_id": "corr-001",
    "surface_status": "available"
  }
}
```

Unknown weights and missing bindings must remain `null` with an explicit
reason. They must not be rendered as zero allocation, because zero is a real
policy outcome.

## Operator Journey And UI State

1. The operator opens `/management/promotion-allocation?tab=real-ranking`.
   The UI shows snapshot time, quarter, surface health, eligibility, exclusion
   reasons, binding scope, current/target/delta, caps, and evidence.
2. The operator opens a proposed line or selects the quarterly-capital tab.
   The UI links the ranking snapshot to exactly one rebalance proposal and
   labels it as a proposal, not an applied allocation.
3. The operator reviews simulation, constraints, affected sleeves/pools,
   rollback target, and increases versus reductions. Missing evidence,
   binding mismatch, or degraded authoritative sources disables submit/apply.
4. Creating the proposal sends an idempotency header. A timeout retry reuses
   the same key; the UI follows the returned `rebalance_id` rather than
   creating a second proposal.
5. The proposal moves to Human Inbox. The workbench shows the authoritative
   approval state and links to the decision detail. Approval is not displayed
   as capital already changed.
6. Apply is enabled only for a valid approved proposal and uses a new apply
   idempotency key plus the bound approval reference. The UI waits for command
   outcome/audit receipt before showing applied state.
7. On failure, the UI preserves proposal and correlation identifiers, shows
   the failed precondition, and offers refresh or governed retry. It never
   optimistically updates current weights.

Emergency containment belongs to PPL-ALLOC-008. This workbench may display an
emergency reduction/freeze proposal, but must never offer promotion or an
allocation increase through that path.

## Frontend Handoff Matrix

| UI element | Source field | Required behavior |
|---|---|---|
| Stage badge | `stage` | Use server enum/mapping; do not derive from capital scope. |
| Eligibility | `eligible`, `exclusion_reasons[]` | Show all reasons; excluded rows may still expose reduce/freeze actions. |
| Binding | `capital_scope`, `capital_scope_id` | Label paper ledger vs canary/live sleeve explicitly; missing means blocked, not zero. |
| Weight columns | `current_weight`, `target_weight`, `delta` | Preserve null; format percentages only after numeric validation. |
| Policy explanation | `cap_reasons[]`, `evidence_refs[]` | Keep caps separate from exclusions and link evidence through governed detail. |
| Proposal state | `rebalance_id`, `status`, `applied` | Distinguish proposal/review/approved/applied and link to `/management/rebalance/:id`. |
| Approval action | verified approval projection | Disable when absent, expired, rejected, mismatched, or sources are degraded. |
| Troubleshooting | snapshot/correlation/surface status | Surface stale/degraded state and retain identifiers in error UI. |

## Acceptance Checks For Composition

- A canary/live line exposes persona, normalized stage, typed capital scope and
  identifier, current/target/delta, eligibility/exclusions, cap reasons, and
  evidence references.
- A paper persona cannot receive positive real allocation merely because it
  has a high ranking; its recommendation is a governed promotion review.
- Proposal creation does not change current weight and returns a stable ID
  under an idempotent retry.
- A live increase cannot be applied without a valid, proposal-bound human
  approval; a string-shaped but invalid reference is rejected.
- Excluded/missing-binding/stale-source rows cannot be submitted as increases.
- Detail shows simulation, constraints, rollback target, audit references, and
  the authoritative proposal/approval/apply states.
- Emergency proposals reject promotion and positive deltas.
- UI tests cover null weights, missing scope ID, degraded surfaces, expired
  approval, idempotent retry, and apply-command failure without optimistic
  mutation.

## Composition Ownership

- PPL-ALLOC-004 owner: decide/adopt BFF response shapes, close the query and
  approval-binding gaps, and provide route tests/examples.
- PPL-ALLOC-006 owner: consume only the adopted contract, implement the
  operator journey and state distinctions, and avoid client-side policy.
- PPL-ALLOC-008 owner: own emergency containment authorization and negative
  tests; this packet only marks the boundary.
- Parent/release owners: record residual gaps and hosted evidence before the
  workflow is claimed complete.
