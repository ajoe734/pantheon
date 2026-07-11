# PPL-ALLOC-006 BFF / Frontend Handoff Follow-Up 5

Task: `PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-5`
Parent: `PPL-ALLOC-006`
Owner: Codex2
Reviewer: Claude
Kind: support-only `bff_handoff_packet`
Generated: 2026-07-11

## Boundary

This packet converts the prior handoff rules into a minimum adapter and test
contract for the parent workbench. It does not define new wire fields, routes,
policy, canonical truth, runtime behavior, or frontend implementation. Any
missing server evidence remains a visible gap for the parent owner; it is not
filled with a client default.

## Minimum Adapter Split

Use resource-specific decoders and query keys. A generic `items` decoder would
misread the rebalance list, while a generic management decoder would blur the
different freshness and degraded states.

| Adapter responsibility | Input shape currently exposed | Output rule |
|---|---|---|
| Ranking and recommendations | object envelope with rows under `data.items` and pagination/metadata beside it | Retain snapshot, surface health, stable persona/binding ids, server states, evidence, caps, and exclusions. |
| Promotion reviews | object envelope with its server-owned review collection | Link only through an explicit returned review id; do not choose a review by recency. |
| Fleet/binding context | persona fleet projection | Enrich by `persona_id`; preserve paper ledger versus real pool/sleeve identity. |
| Allocation evaluation | object under `data`, including `lines` and `applied: false` | Preserve the complete preview as proposal input; never map preview success to a durable state. |
| Rebalance list | bare array under `data` | Decode separately and retain the server `rebalance_id`; list data cannot authorize apply. |
| Rebalance detail | object under `data` | Treat as the review/apply prerequisite for simulation, constraints, rollback, state, and approval data. |
| Mutation receipt | command/resource ids and links returned by the route | Advance only the proven workflow stage and retain the receipt across refresh failures. |

The client row may compose these projections, but query health stays per
surface. An enrichment error must not delete the ranking spine or convert an
unknown value into `false`, zero, ineligible, rejected, or applied.

## Fixture Set For Parent Tests

The parent should keep a small fixture family rather than one all-success
workbench payload:

1. `ranking_ready_recommendations_degraded`: ranking rows remain visible,
   recommendation actions are unavailable, and no empty-success claim appears.
2. `duplicate_display_names`: two persona ids share a label; review and
   rebalance links remain isolated by stable ids.
3. `submit_replay`: recommendation submission returns an existing review;
   the adapter restores one link and reuses the intent idempotency key.
4. `preview_complete`: evaluation returns current/target/delta, caps,
   exclusions, evidence, simulation, constraints, rollback target, and
   `applied: false`; proposal construction preserves all of them.
5. `preview_incomplete`: one required identity or audit field is missing;
   proposal creation is disabled and the missing evidence is named.
6. `rebalance_list_ready_detail_error`: the proposal remains inspectable and
   linkable, but review/apply controls are unavailable.
7. `apply_live_increase_409`: current weight is unchanged and the approval
   precondition is shown without rewriting it as a network error.
8. `apply_accepted_readback_pending`: command receipt is retained, workflow is
   `apply submitted`, and only authoritative allocation/binding readback may
   advance it to applied.
9. `dry_run_create`: synthetic/non-durable ids are labelled dry-run and are
   never offered as review or apply targets.
10. `emergency_helper_absent`: no direct mutation fallback appears; the row
    explains that the governed action helper is unavailable.

Each fixture should assert the displayed workflow label, enabled capabilities,
preserved identifiers, query-health label, and absence of optimistic capital
mutation. HTTP `401/403`, `404`, `409`, and `422` need distinct expectations.

## Operator Journey Assertions

```text
recommendation returned  != review submitted
review approved          != proposal created
proposal created         != apply submitted
apply submitted          != applied confirmed
```

- Inspect and explanation remain available when an unrelated enrichment is
  degraded.
- A write timeout retries the same operator intent with the same idempotency
  key; a deliberate new intent gets a new key.
- The UI retains server receipts and ids when a follow-up query fails.
- Approval and command acceptance never patch `current_weight` optimistically.
- `applied confirmed` requires refreshed authoritative capital/binding
  evidence, not elapsed time, a success toast, or proposal status guessing.
- Emergency actions require the installed governed helper, reason/evidence,
  and a risk-decreasing action; this sidecar supplies no endpoint fallback.

## Explicit Parent Blockers / Decisions

These remain parent-owned integration decisions and should be visible in the
implementation PR rather than silently resolved in adapters:

1. Identify the installed `execute-plans` governed action helper for emergency
   containment. Until confirmed, keep emergency mutation unavailable.
2. Identify which authoritative allocation or binding query proves that an
   accepted apply command changed capital. Until then, stop at
   `apply submitted`.
3. Preserve per-surface freshness because there is no atomic ranking/review/
   fleet/rebalance snapshot.
4. Report absent stable cross-resource ids as a BFF query gap. Do not join by
   display name, array position, newest record, or matching weights.
5. Keep legacy navigation/redirect pruning in `PPL-ALLOC-007`; this packet
   does not make route migration part of the parent adapter contract.

## Review And Absorption

Owned here: support-only adapter boundaries, fixture scenarios, operator-state
assertions, and an explicit blocker list.
Not changing: L1/L2 truth, BFF route or schema guarantees, BFF/runtime/
registry/governance implementation, `execute-plans` source, or parent task
lifecycle.
Composes with: `PPL-ALLOC-003` binding reads, `PPL-ALLOC-004` allocation
policy, parent `PPL-ALLOC-006`, and the preceding PPL-ALLOC-006 handoff
packets.

Claude should review that the fixtures are fail-closed consumer expectations
and do not imply new server guarantees. The parent owner decides which cases
to absorb into frontend adapters and component/integration tests.

## Sources Reviewed

- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`
- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md`
- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md`
- `docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap/PPL-ALLOC-006-fe-promotion-allocation-workbench.md`
- `services/control-plane/bff/main.py`
