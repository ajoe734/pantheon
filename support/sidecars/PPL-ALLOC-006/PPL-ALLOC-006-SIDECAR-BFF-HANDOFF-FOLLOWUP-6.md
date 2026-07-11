# PPL-ALLOC-006 BFF / Frontend Handoff Follow-Up 6

Task: `PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-6`  
Parent: `PPL-ALLOC-006`  
Owner: Codex2  
Reviewer: Claude  
Kind: support-only `bff_handoff_packet`  
Generated: 2026-07-11

## Boundary

This packet gives the parent owner an absorption order and traceability matrix
for the existing BFF handoff guidance. It changes no route, schema, policy,
canonical truth, runtime/registry/governance implementation, or
`execute-plans` source. An item marked as a gap remains unavailable until its
owning implementation supplies evidence; the frontend must not fill it with a
default or heuristic.

## Recommended Absorption Order

1. **Install resource-specific decoders.** Keep ranking/recommendations,
   promotion reviews, fleet/bindings, allocation evaluation, rebalance list,
   rebalance detail, and mutation receipts separate. In particular,
   ranking/recommendation rows use `data.items`, while the rebalance list is a
   bare array under `data`.
2. **Create the ranking spine.** Render rows from ranking and enrich only by
   explicit stable ids. Preserve independent loading, degraded, stale, and
   error state for every contributing query.
3. **Add receipt-backed workflow state.** Persist recommendation, review,
   rebalance, and command ids separately. A mutation advances only the stage
   proven by its response; refresh failure must not erase an accepted receipt.
4. **Gate proposal construction.** Carry the ranking snapshot, complete
   evaluation lines, identities, caps/exclusions, evidence, simulation,
   constraints, and rollback target unchanged. Missing input disables create
   and names the gap.
5. **Gate apply on current detail and approval.** Never expose apply from list
   data alone. A live increase without the required approval remains blocked,
   and an accepted command renders `apply submitted`.
6. **Add authoritative readback before `applied`.** Keep displayed current
   weight unchanged until the binding/allocation owner supplies a refreshed
   read that proves the effect.
7. **Expose emergency actions only after helper discovery.** The parent must
   identify the installed governed action helper. Until then, show the
   capability as unavailable and provide no direct mutation fallback.

This order lets the parent deliver truthful read-only and review flows before
capital-affecting controls are enabled. It does not relax any acceptance gate.

## Traceability Matrix

| Parent workbench behavior | BFF evidence consumed | Frontend assertion | Status for absorption |
|---|---|---|---|
| Paper candidate inspection | Ranking `data.items`, recommendation projection, surface metadata | Ranking remains visible if recommendation enrichment is degraded; submit is unavailable | Ready as consumer rule |
| Recommendation submission | Server recommendation id, idempotent submit response, returned review/inbox/command ids | First accept and replay produce one review link; state becomes `review submitted`, never approved | Ready as consumer rule |
| Review decision display | Explicit review id and review/decision read or receipt | Approved/rejected is distinct from proposal creation and capital application | Ready as consumer rule |
| Real allocation preview | Authoritative snapshot and evaluation `data.lines` with `applied: false` | Current/target/delta, caps, exclusions, evidence, and missing fields are rendered without recomputation | Ready when complete input exists |
| Durable proposal creation | Complete preview plus pool identity, simulation, constraints, rollback target and idempotency | Durable returned `rebalance_id` is retained; dry-run id is never review/apply eligible | Ready when complete input exists |
| Proposal review/apply affordance | Successful current rebalance detail, apply capability, role/confirmation data, approval reference when required | List-only or stale detail disables apply; `409` and `422` retain their server meaning | Ready as fail-closed rule |
| Apply command accepted | Apply receipt and command/audit links | State becomes `apply submitted`; current weight is not patched | Ready as consumer rule |
| Allocation applied | Refreshed authoritative capital/binding evidence proving the new allocation | Only proof-backed readback may produce `applied confirmed` | Parent must identify proof query |
| Emergency containment | Installed governed action helper, supported decreasing action, reason/evidence and role gates | No promote/increase action and no direct REST fallback | Parent must identify helper |

## Parent-Owned Decisions Before Merge

The implementation PR should answer these explicitly:

- Which `execute-plans` adapter owns each response envelope and its query key?
- Which stable ids are present for every intended cross-surface join, and which
  missing joins stay visibly unavailable?
- Which read is authoritative for confirming that a rebalance apply changed
  current capital or binding state?
- Which installed governed action helper powers emergency containment?
- Which per-surface freshness/degraded metadata is shown to the operator?
- Which component or integration tests cover idempotent replay, partial
  query failure, incomplete preview, detail failure, live-increase `409`, and
  accepted-apply/readback-pending?

A missing answer is a scoped integration blocker, not permission to infer a
contract in the client.

## Minimum Review Evidence

Claude can review the parent absorption against this small evidence bundle:

1. Adapter tests prove the different response envelopes and stable-id joins.
2. A component test renders ranking through recommendation/review degradation.
3. A replay test preserves one operator intent and one returned review link.
4. A proposal test proves every audit field survives preview-to-create.
5. Apply tests prove detail gating, live-increase approval failure, unchanged
   current weight, and the `apply submitted` intermediate state.
6. An emergency test proves the absence of any fallback promote/increase
   mutation when the governed helper is unavailable.

Hosted evidence is still owned by the parent/full-loop closeout; this support
packet itself claims no runtime delivery.

## Review And Absorption

Owned here: support-only absorption order, traceability, parent decisions, and
review evidence.  
Not changing: L1/L2 truth, BFF routes or schemas, BFF/runtime/registry/
governance implementation, frontend source, navigation, or parent lifecycle.  
Composes with: `PPL-ALLOC-003` binding reads, `PPL-ALLOC-004` allocation
policy, parent `PPL-ALLOC-006`, and the preceding PPL-ALLOC-006 BFF handoff
packets.

Claude should verify that every matrix row is a consumer expectation or a
visible gap, not a newly asserted server guarantee. The parent owner decides
which items to absorb and owns the implementation and hosted proof.

## Sources Reviewed

- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`
- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md`
- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md`
- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md`
- `docs/04/pantheon_persona_promotion_allocation_gap_2026-07-07/PERSONA_PROMOTION_ALLOCATION_GAP_SPEC.md`
- `docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap/PPL-ALLOC-006-fe-promotion-allocation-workbench.md`
- `services/control-plane/bff/main.py`
