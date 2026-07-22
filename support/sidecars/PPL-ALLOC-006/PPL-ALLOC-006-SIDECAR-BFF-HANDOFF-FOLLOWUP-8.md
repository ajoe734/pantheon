# PPL-ALLOC-006 BFF / Frontend Handoff Follow-Up 8

Task: `PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-8`
Parent: `PPL-ALLOC-006`
Owner: Codex2
Reviewer: Claude
Kind: support-only `bff_handoff_packet`
Generated: 2026-07-11

## Boundary

This packet is a post-merge absorption audit for the parent workbench. It
compares the merged parent delivery with the preceding BFF/frontend handoff
rules and records what the operator journey may truthfully claim. It changes
no canonical truth, BFF route or schema, frontend source, policy, runtime,
registry, governance implementation, or parent lifecycle state. The parent
owner decides whether to turn any residual item into implementation work.

## Delivery Evidence Observed

- Pantheon PR `#3142` is merged into `dev` at `644b5a6c8`; that merge is a
  parent closeout record and does not itself contain frontend implementation.
- The unmerged `execute-plans` branch `task/PPL-ALLOC-006-workbench` (PR
  `#251`) contains the richer PPL-ALLOC-006 implementation, including
  `RealRankingPanel` coverage and fix commit `436aa32`, which evaluates
  allocation policy over the full input row set.
- That same unmerged branch routes paper candidates, real ranking, quarterly
  capital, formula policy, and a read-only emergency-actions panel through
  `/management/promotion-allocation`. `EmergencyActionsPanel.tsx` reads Human
  Inbox containment items and links to governed decision detail; it exposes no
  emergency mutation control. The `execute-plans` `origin/dev` shell does not
  yet contain this richer branch implementation.
- Existing frontend adapters expose quarterly-ranking recommendation submit
  and rebalance list/detail paths, and the BFF still exposes the ranking,
  recommendation, review, allocation-evaluate, and rebalance route families
  inventoried by the earlier packets.

These facts prove useful composition surfaces exist. They do not by themselves
prove every mutation-to-readback transition in the full operator journey.

## Absorption Verdict

| Journey slice | Observed absorption | Truthful parent claim | Residual gate |
|---|---|---|---|
| Unified entry | The unmerged PR #251 branch has paper, real-ranking, quarterly-capital, formula-policy, and read-only emergency-actions tabs | If PR #251 is absorbed, the unified workbench can be the primary inspection entry for those surfaces | Do not describe branch-only UI as merged delivery; the emergency tab is inspection-only |
| Recommendation submit | Governed submit adapter and tests exist | A recommendation can be submitted or reported local-only/write-disabled | Preserve returned review/inbox/command ids; submission is not approval |
| Real ranking / preview | Dedicated panel and full-input policy evaluation test exist | Current/target allocation can be evaluated as an advisory preview | Keep `applied: false`, caps, exclusions, and evidence server-owned |
| Quarterly capital | Capital-pool and rebalance list surfaces are composed into the tab | Operators can inspect and navigate durable rebalance records | List presence alone cannot enable apply; require current detail and approval evidence |
| Apply completion | No complete receipt-to-authoritative-readback proof was established by this audit | At most `apply submitted` after a successful command receipt | Display `applied confirmed` only after named binding/allocation readback proves the change |
| Emergency containment | The same unmerged PR #251 branch has a read-only `EmergencyActionsPanel` backed by Human Inbox data and governed decision-detail links, with no mutation call | Operators can inspect containment records from that branch UI, but cannot initiate emergency containment there | Wait for the governed PPL-ALLOC-008 helper and negative-test evidence; expose no direct fallback mutation |

## Parent Follow-Up Contract

The merged parent delivery should retain the following fail-closed rules:

1. Keep recommendation, review decision, proposal, apply command, and applied
   readback as separate records and labels.
2. Keep ranking, recommendation, review, binding, and rebalance query health
   independent. A partial failure must not erase a proven receipt or fabricate
   a successful empty result.
3. Join only through explicit server identifiers. Never use display name,
   array position, newest review, or matching weights as a relationship.
4. Require a successful current rebalance-detail read before apply; list data
   cannot supply simulation, constraints, rollback, or approval truth.
5. Retain the old `current_weight` after apply acceptance until an
   authoritative capital/binding read proves the new value.
6. Keep emergency containment unavailable until the governed helper supports
   reason/evidence, authorization, and risk-decreasing-only actions.

## Reviewer Acceptance

Claude can approve this support packet when the evidence statements remain
descriptive rather than promotional and the two unresolved capabilities stay
explicitly closed:

- no `applied confirmed` state without authoritative readback; and
- no emergency action without the governed helper and its negative tests.

The parent owner may absorb this as a residual-risk checklist or create narrow
follow-up implementation tasks. This sidecar does not reopen or redefine the
merged parent task.

## Review And Composition

Owned here: support-only post-merge absorption audit and residual fail-closed
handoff.
Not changing: L1/L2 truth, BFF/frontend implementation, route contracts,
runtime/registry/governance behavior, dependency ownership, or lifecycle
state.
Composes with: parent `PPL-ALLOC-006`, `PPL-ALLOC-003` binding reads,
`PPL-ALLOC-004` allocation semantics, `PPL-ALLOC-008` emergency containment,
and the preceding PPL-ALLOC-006 BFF handoff packets.

## Sources Reviewed

- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-7.md`
- `docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap/PPL-ALLOC-006-fe-promotion-allocation-workbench.md`
- `services/control-plane/bff/main.py`
- `execute-plans:src/management/pages/oversight/PromotionAllocation.tsx`
- `execute-plans:src/management/pages/oversight/RealRankingPanel.tsx`
- `execute-plans:src/management/pages/oversight/RealRankingPanel.test.tsx`
- `execute-plans:src/lib/bff-v1/management.ts`
