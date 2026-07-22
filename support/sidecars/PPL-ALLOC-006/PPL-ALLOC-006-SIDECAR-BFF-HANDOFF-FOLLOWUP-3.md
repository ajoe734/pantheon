# PPL-ALLOC-006 BFF / Frontend Handoff Follow-Up 3

Task: `PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-3`
Parent: `PPL-ALLOC-006`
Owner: Codex
Reviewer: Claude
Kind: support-only `bff_handoff_packet`
Generated: 2026-07-11

## Boundary

This packet turns the existing route and envelope inventory into a frontend
query-orchestration handoff. It adds no endpoint, policy, runtime behavior,
registry/governance truth, or `execute-plans` implementation. The parent owner
decides whether to absorb these rules into adapters and tests.

## Query Orchestration

Load the workbench as independent projections. Ranking is the row spine; the
other queries enrich it but must not block or silently rewrite it.

| Query | Suggested cache identity | Join/output rule |
|---|---|---|
| Quarterly ranking | filters plus quarter/snapshot when supplied | Create rows from `data.items`; retain the response snapshot id and surface metadata. |
| Recommendations | the same ranking filters | Join only by stable persona/binding/snapshot identifiers. Missing data means `recommendation unavailable`, not `no recommendation`. |
| Persona fleet | persona filters | Enrich stage and binding identity by `persona_id`; never translate a paper ledger into a real pool/sleeve. |
| Promotion reviews | review/persona filters | Join through an explicit review id returned by a recommendation submission or supplied by the BFF. Do not select the newest review heuristically. |
| Rebalances | pool/status filters | Link only by a returned/stored `rebalance_id` or an explicit BFF relationship. Never match by similar target weights. |
| Rebalance detail | `rebalance_id` | Owns proposal simulation, constraints, rollback target, approval reference, and proposal state. |

Suggested client row shape (names are illustrative, not a new wire contract):

```text
identity: persona_id, ranking_snapshot_id, capital_scope, pool_or_sleeve_id
ranking: current_weight, target_weight, delta, cap_reasons, evidence_refs
workflow: recommendation_id, review_id, rebalance_id, command_id
states: recommendation_state, review_state, proposal_state, command_state
freshness: per-surface loaded_at/degraded/error
```

Never use array position, display name, or a locally generated id as a
cross-surface join key. When an expected stable key is absent, preserve the
base row, mark that enrichment unavailable, and omit the action/deep link.

## Mutation And Refresh Rules

Each write changes only the state proven by its receipt. Refresh dependent
reads instead of optimistic capital updates.

| Mutation receipt | Immediate truthful UI state | Reads to refresh |
|---|---|---|
| Recommendation submit | `review submitted` with returned review/inbox/command links | recommendations and promotion reviews; ranking may refresh for server-owned governance state |
| Review decision | `approved` or `rejected` with decision receipt | review detail/list and recommendations; do not alter allocation weight |
| Allocation evaluate | `target calculated`, always advisory | none required; carry snapshot, lines, caps, exclusions, evidence into proposal input |
| Rebalance create | `proposal created` with durable `rebalance_id`; dry-run remains non-durable | rebalance list and returned detail |
| Rebalance apply | `apply submitted` with command receipt | rebalance detail/list plus the authoritative capital/binding read; only that read may justify `applied` |

Idempotent replay is recovery, not a second local event. If recommendation
submit returns the existing review, or proposal creation returns an existing
resource, replace/merge by the server id and do not append a duplicate row.

Preserve the idempotency key for the lifetime of one operator intent. Generate
a new key only after the operator deliberately starts a new intent, not after a
timeout, render, retry, or tab switch.

## Mixed Freshness And Failure States

There is no atomic aggregate snapshot. The workbench must expose enough
freshness to avoid implying that independently fetched surfaces agree.

- A ranking success plus recommendation failure is a visible ranking row with
  unavailable recommendation controls, not an empty candidate table.
- A review failure does not roll a submitted recommendation back to
  `recommended`; retain the returned review id and show review status unknown.
- A rebalance-detail failure does not allow apply from list data alone; require
  the detail needed to show simulation, constraints, rollback, and approval.
- A stale/unknown id (`404`) should retain the originating receipt for audit
  and offer refresh/navigation recovery, not silently choose another record.
- `409` remains an unmet precondition and `422` remains incomplete/unsafe
  input. Neither should be relabelled as a transient network failure.
- Degraded or write-disabled metadata disables only the affected action or
  enrichment. It must not fabricate a successful empty result.

If different surfaces expose timestamps, show them per surface. Do not compute
a synthetic `last updated` timestamp that suggests an atomic snapshot.

## Parent Adapter Acceptance Cases

1. Ranking renders when recommendations, reviews, or fleet enrichment fail,
   with each missing enrichment labelled independently.
2. Two personas with the same display name never cross-link reviews or
   rebalances; joins use stable identifiers only.
3. A missing review id never falls back to the latest review for that persona.
4. A recommendation-submit replay restores one existing review link and does
   not duplicate the row or generate a new idempotency key.
5. Allocation evaluation output retains snapshot id, caps, exclusions, and
   evidence unchanged when constructing a proposal request.
6. A dry-run proposal is labelled non-durable and is not offered as an apply
   target.
7. Apply success changes the row to `apply submitted`; current weight remains
   unchanged until an authoritative binding/allocation refresh proves it.
8. A partial refresh cannot erase the command receipt or advance the workflow
   to `applied`.
9. Detail unavailable, `409`, `422`, and degraded/write-disabled states have
   distinct assertions and operator recovery guidance.
10. Emergency actions remain outside this composition unless the parent uses
    an installed governed action helper; no direct mutation route is invented.

## Composition And Review

Owned here: support-only frontend orchestration, join, refresh, and failure
guidance.
Not changing: canonical policy, BFF contracts or implementation,
runtime/registry/governance behavior, frontend source, or parent lifecycle.
Composes with: `PPL-ALLOC-003` capital/binding reads, `PPL-ALLOC-004`
allocation policy, the parent `PPL-ALLOC-006` workbench, and the two earlier
PPL-ALLOC-006 BFF handoff packets.

Claude should verify that the packet stays support-only and that the parent can
use the acceptance cases without inferring new BFF guarantees. The parent
owner decides which guidance becomes frontend adapter/test work.

## Sources Reviewed

- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`
- `docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap/PPL-ALLOC-006-fe-promotion-allocation-workbench.md`
- `services/control-plane/bff/main.py`
