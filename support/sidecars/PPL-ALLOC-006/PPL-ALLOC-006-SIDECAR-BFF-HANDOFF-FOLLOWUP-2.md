# PPL-ALLOC-006 BFF / Frontend Handoff Follow-Up 2

Task: `PPL-ALLOC-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`
Parent: `PPL-ALLOC-006`
Owner: Codex
Reviewer: Claude
Kind: support-only `bff_handoff_packet`
Generated: 2026-07-11

## Boundary

This packet refines the earlier handoff with the response envelopes, join
rules, write preconditions, and acceptance cases needed by the parent
workbench. It changes no canonical truth, BFF/runtime/registry/governance
implementation, or `execute-plans` source. The parent owner decides what to
absorb.

## Integration Shape

The workbench is a client-side composition, not one aggregate BFF resource.
Keep each query's loading, degraded, and error state independent.

| Concern | Route | Response location / integration rule |
|---|---|---|
| Ranking | `GET /bff/management/quarterly-ranking` | Rows are in `data.items`; pagination is `page_info`; inspect `meta.surfaces.quarterly_ranking` before presenting a complete snapshot. |
| Recommendations | `GET /bff/management/quarterly-ranking/recommendations` | Rows are in `data.items`; retain `recommendation_id`, `action_id`, persona/binding identifiers, evidence refs, and governance state supplied by the BFF. |
| Explanation | `GET /bff/management/quarterly-ranking/drilldown?personaId=...` | The route requires `personaId` or `persona_id`; contribution fields are exposed both inside `data` and as top-level compatibility projections. Prefer the `data` object. |
| Promotion reviews | `GET /bff/management/promotion-reviews` | This is an object envelope, not a bare list. Consume the returned review collection inside `data` and use `page_info`; do not join by array position. |
| Fleet/bindings | `GET /bff/management/persona-fleet` | Join by stable `persona_id`, then preserve the BFF-provided pool/sleeve/runtime identities. Never infer real capital from a paper ledger. |
| Allocation preview | `POST /bff/management/allocation-policy/evaluate` | Request `{ranking_snapshot_id, rows}`; response is `data.lines` plus `data.applied: false`. It is a calculation, never an apply receipt. |
| Rebalance list/detail | `GET /bff/rebalances`, `GET /bff/rebalances/{id}` | List data is a bare array in `data`; detail is an object in `data`. Do not reuse the ranking envelope decoder. |

Recommended normalized row identity:

```text
persona_id + quarter/ranking_snapshot_id + capital_scope + pool_or_sleeve_id
```

Recommendation, review, rebalance, and command identifiers are links in the
workflow, not interchangeable primary keys. If one is missing, render that
stage as unavailable rather than guessing a link.

## Write Handoff

### Submit a recommendation

`POST /bff/management/quarterly-ranking/recommendations/{recommendation_id}/submit`
requires an idempotency header and an operator/approver/admin identity. The
response may be `202` for the first submission or `200` for an existing
submission replay. Treat both as successful submission only when the response
contains the linked `review_id`/`promotion_review_id`; preserve `command_id`,
`human_inbox_id`, and `links`. The response explicitly reports no live-capital
or runtime mutation.

### Create a quarterly rebalance proposal

`POST /bff/rebalances` requires an idempotency header and
`capital_pool_id`. A complete policy-backed proposal must also include:

```text
ranking_snapshot_id
lines[]: persona_id, stage, capital_scope, current_weight, target_weight,
         delta, cap_reasons, evidence_refs
simulation
constraints
rollback_target
```

The response returns `rebalance_id` and command metadata. A dry run returns
HTTP `200` with synthetic `dryrun-*` identifiers; it must be labelled dry-run
and must not be linked as a durable proposal. A normal accepted create is not
approval and not application.

### Apply a proposal

`POST /bff/rebalances/{rebalance_id}/apply` requires an idempotency header.
If any `live_running` line increases weight, the BFF returns `409` unless an
`approval_ref` is present in the request or stored proposal. A successful
response only authorizes/submits a command. The current route does not itself
provide authoritative execution completion, so the UI state must remain
`apply submitted` until a later read surface proves the binding/allocation
changed.

## Query Gaps The Parent Must Keep Visible

1. There is no atomic workbench snapshot across ranking, reviews, fleet, and
   rebalances. Display per-surface timestamps/degraded state; do not present a
   mixed snapshot as fully current.
2. The ranking/recommendation surfaces do not guarantee a rebalance id. The
   parent must retain the id returned by proposal creation and refresh the
   rebalance list/detail rather than synthesize an id.
3. The allocation evaluation route returns calculated lines but no durable
   proposal. Losing its `ranking_snapshot_id`, caps, exclusions, or evidence
   while creating the proposal breaks auditability.
4. Rebalance apply acceptance is not execution proof. Until an authoritative
   applied marker exists in refreshed reads, `approved`, `apply submitted`,
   and `applied` must remain distinct.
5. Emergency containment has server-side line validation, but this slice does
   not establish a new workbench-specific command route. Use the existing
   governed action helper selected by the parent; never invent a direct REST
   mutation or offer an emergency increase/promotion.

## Operator Journey Contract

| Operator step | UI state after success | Must not claim |
|---|---|---|
| Inspect paper row | `recommended` or server-provided eligibility state | review submitted |
| Submit recommendation | `review submitted`, with review/inbox link | approved or promoted |
| Record review decision | `approved` or `rejected`, with decision receipt | capital applied |
| Evaluate allocation | `target calculated`, with caps/exclusions | proposal created |
| Create proposal | `proposal created`, with `rebalance_id` | approved or applied |
| Submit apply | `apply submitted`, with command receipt | applied |
| Refresh authoritative allocation/binding | `applied` only when the read model proves it | optimistic completion |

For errors, keep BFF semantics visible: `401/403` identity/role, `404` stale or
unknown id, `409` unmet approval/precondition, and `422` incomplete or unsafe
proposal. Do not collapse these into a generic disabled action.

## Parent Acceptance Matrix

- Ranking/recommendation decoders read `data.items`; rebalance-list decoder
  reads the bare `data` array; rebalance detail reads the `data` object.
- A degraded recommendation query does not erase an independently loaded
  ranking row or fabricate `no recommendation`.
- Duplicate recommendation submission (`200` replay) restores the existing
  review link without duplicating a local row.
- Allocation evaluation always renders `applied: false`, current/target/delta,
  exclusions, and every `cap_reasons` value without client recomputation.
- Proposal create sends every required audit field and preserves the returned
  `rebalance_id`; dry-run ids are visibly non-durable.
- Live increase apply without approval renders the BFF `409` precondition and
  does not optimistically alter weight.
- Apply acceptance stays pending until refreshed authoritative readback proves
  completion.
- Recommendation, review, approval, proposal, command, and applied states have
  distinct labels and test assertions.
- Emergency controls require reason/evidence, expose only risk-decreasing
  actions, and surface server policy rejection unchanged.

## Composition And Review

Owned here: support-only integration details and fail-closed frontend
acceptance guidance.
Not changing: canonical policy, route contracts, BFF/runtime implementation,
frontend code, navigation pruning, or parent lifecycle state.
Composes with: `PPL-ALLOC-003` binding reads, `PPL-ALLOC-004` allocation
policy, the parent `PPL-ALLOC-006` workbench, and the earlier
`PPL-ALLOC-006-SIDECAR-BFF-HANDOFF` packet.

Claude should review that this packet remains support-only and that its
envelope/write guidance matches the current BFF implementation. After review,
the parent owner decides whether to absorb the matrix into frontend adapters,
tests, or task notes.

## Sources Reviewed

- `support/sidecars/PPL-ALLOC-006/PPL-ALLOC-006-SIDECAR-BFF-HANDOFF.md`
- `docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap/PPL-ALLOC-006-fe-promotion-allocation-workbench.md`
- `services/control-plane/bff/main.py`
