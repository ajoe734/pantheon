# PPL-ALLOC-006 BFF / Frontend Handoff Follow-Up 4

Task: `PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-4`
Parent: `PPL-ALLOC-006`
Owner: Codex
Reviewer: Claude
Kind: support-only `bff_handoff_packet`
Generated: 2026-07-11

## Boundary

This packet closes the remaining handoff gap between independently loaded BFF
projections and the actions exposed by the Promotion & Allocation workbench.
It defines frontend capability gates and recovery states; it does not add a
route, wire-field guarantee, policy, canonical truth, runtime behavior, or
`execute-plans` implementation. The parent owner decides what to absorb.

## Capability Gates

An action is enabled only when its own required server evidence is loaded and
non-degraded. A successful ranking query does not make every row actionable.

| Capability | Evidence required before enabling | Fail-closed result |
|---|---|---|
| Submit paper/canary recommendation | Server `recommendation_id`, stage/action target, evidence references, submit link or installed adapter support, writable surface metadata, and operator identity | Keep the row inspectable; label submission unavailable and identify the missing/degraded source. |
| Evaluate real allocation | Authoritative ranking snapshot id plus eligible canary/live rows with current weight, capital scope, pool/sleeve identity, and evidence | Do not manufacture zero weights, eligibility, or a snapshot id; disable evaluation for the incomplete universe. |
| Create rebalance proposal | Evaluation output with `applied: false`, all lines, cap/exclusion reasons, evidence, simulation, constraints, rollback target, and durable pool identity | Preserve the preview for inspection, but do not submit a partial proposal or silently drop excluded rows. |
| Review proposal | Durable `rebalance_id` and successful detail read containing proposal state, simulation, constraints, rollback target, and approval data | List rows may link to recovery, but cannot expose apply from list data alone. Dry-run ids are never review/apply targets. |
| Apply proposal | Successful current detail read, apply capability, idempotency support, role/confirmation prerequisites, and required `approval_ref` for any live increase | Disable apply and surface the exact missing precondition. Never infer approval from a recommendation or promotion-review label. |
| Mark applied | Apply receipt plus refreshed authoritative binding/allocation evidence showing the changed allocation | Remain `apply submitted`; retain command/audit links and unchanged displayed current weight. |
| Emergency containment | Installed governed action helper, supported risk-decreasing action, reason/evidence, role/confirmation prerequisites, and writable surface | Offer no direct fallback mutation. Never convert containment into promotion or capital increase. |

Missing fields are `unknown/unavailable`, not false, zero, empty, or
ineligible. A client default must not become a policy decision.

## Row State Machine

Keep workflow state and query health orthogonal:

```text
workflow:
  recommended -> review_submitted -> approved | rejected
  target_calculated -> proposal_created -> apply_submitted -> applied_confirmed

query health per surface:
  idle | loading | ready | degraded | error | stale
```

A query-health transition must not move the workflow backward or forward. For
example, a failed review refresh leaves the row `review_submitted` with
`review status unavailable`; it does not return it to `recommended`. A failed
binding refresh after apply leaves `apply_submitted`, not `applied` or
`proposal_created`.

Persist server-returned identifiers and receipts independently of cached
enrichment. Refresh failure must never erase `review_id`, `rebalance_id`,
`command_id`, approval receipt, or audit links already proven by a response.

## Query-Gap Decisions For The Parent

1. **No atomic snapshot:** ranking, recommendations, fleet/bindings, reviews,
   and rebalances can represent different moments. Show per-surface freshness
   and gate mutations on the evidence they consume; do not publish one
   synthetic workbench timestamp.
2. **No guaranteed cross-resource link:** only explicit ids/links may connect
   recommendation, review, rebalance, and command records. A display name,
   array position, latest-record heuristic, or matching weights is not a join.
3. **No authoritative apply completion in the command receipt:** the receipt
   proves submission, while an allocation/binding read proves effect. If that
   read cannot prove the target, the UI must not use elapsed time or proposal
   status guessing to mark completion.
4. **No sidecar-defined emergency route:** emergency UI remains unavailable
   until the parent locates the installed governed action adapter. This packet
   does not authorize a workbench-only endpoint.
5. **No client policy reconstruction:** stage, eligibility, caps, exclusions,
   target/delta, approval, and applied truth stay server-owned. Missing server
   values remain visible gaps and should be reported to the owning BFF slice.

## Operator Recovery Contract

| Condition | Operator-visible recovery |
|---|---|
| Recommendations degraded while ranking is ready | Keep ranking rows and evidence visible; retry only recommendations and disable submit. |
| Submit times out | Retry with the same idempotency key; merge an idempotent replay by returned review id. |
| Review id returns `404` | Retain the submit receipt/id, show stale-link recovery, and refresh the originating recommendation; never select another review. |
| Evaluation succeeds but proposal create fails | Keep the exact preview and intent idempotency key for retry; do not label it durable. |
| Proposal detail is stale/error | Retain the list row and deep-link identity, disable review/apply, and retry detail. |
| Apply returns `409` | Show the approval/precondition failure unchanged; do not alter current or target weights. |
| Apply returns success but readback fails | Show `apply submitted`, receipt, and pending verification; retry authoritative readback. |
| Any write surface is local-only/dry-run/disabled | Label the mode at the action and result; never present its ids or state as production-durable. |

## Frontend Acceptance Scenarios

1. Ranking success plus recommendation failure renders ranking evidence and an
   unavailable submit control, not an empty candidate list.
2. A row missing `capital_scope` or pool/sleeve identity cannot enter the
   allocation evaluation universe, and the UI identifies the missing field.
3. Proposal creation is blocked if caps, exclusions, evidence, simulation,
   constraints, or rollback target would be lost between preview and request.
4. List success plus detail failure never exposes apply.
5. Two writes caused by timeout/retry reuse one intent idempotency key and
   merge by server id rather than appending duplicate workflow stages.
6. A `review_submitted` row remains submitted across review-query failure and
   retains its review/inbox link.
7. Approval never changes displayed current weight; apply acceptance changes
   only the command state to `apply submitted`.
8. `applied_confirmed` requires refreshed binding/allocation evidence and
   cannot be reached from a timer, optimistic cache patch, or command receipt.
9. `401/403`, `404`, `409`, `422`, degraded, stale, and write-disabled states
   have distinct labels and recovery behavior.
10. Emergency controls are absent or explicitly unavailable without the
    governed helper and never expose promote/increase actions.

## Composition And Review

Owned here: support-only capability gating, query-gap decisions, recovery
states, and frontend acceptance guidance.
Not changing: L1/L2 canonical truth, BFF routes/contracts or implementation,
runtime/registry/governance behavior, frontend source, routing, or parent task
lifecycle.
Composes with: `PPL-ALLOC-003` binding reads, `PPL-ALLOC-004` allocation
policy, parent `PPL-ALLOC-006`, and the preceding PPL-ALLOC-006 BFF handoff
packets.

Claude should verify that every action gate is fail-closed and remains a
consumer rule rather than a new server guarantee. The parent owner decides
which scenarios become adapter/component tests.

## Sources Reviewed

- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`
- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md`
- `docs/04/pantheon_persona_promotion_allocation_gap_2026-07-07/PERSONA_PROMOTION_ALLOCATION_GAP_SPEC.md`
- `docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap/PPL-ALLOC-006-fe-promotion-allocation-workbench.md`
- `services/control-plane/bff/main.py`
