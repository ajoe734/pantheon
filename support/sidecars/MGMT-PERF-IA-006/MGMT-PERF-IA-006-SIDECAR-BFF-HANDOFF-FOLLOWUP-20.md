# MGMT-PERF-IA-006 BFF Handoff Follow-up 20

| Field | Value |
|---|---|
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-20` |
| Parent task | `MGMT-PERF-IA-006` — Contextual integration |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar owner / reviewer | `Codex` / `Antigravity` |
| Date | `2026-07-12` |
| Mutates canonical truth | `false` |

This support-only packet defines the evidence intake that should occur after
the Wave 1 destinations merge and before the parent composes contextual links.
It does not add or change routes, schemas, runtime behavior, frontend code,
registry/governance behavior, or canonical truth.

## 1. Current Intake Gate

At preparation time the parent remains `todo` and depends on
`MGMT-PERF-IA-003`, `MGMT-PERF-IA-004`, and `MGMT-PERF-IA-005`.
`MGMT-PERF-IA-003` is blocked pending human merge of execute-plans PR `#261`,
and `MGMT-PERF-IA-005` is `review_approved` but still pending human merge of
execute-plans PR `#260`. Therefore neither provisional branch behavior nor a
query value retained in the browser is acceptable fulfillment evidence for
the parent.

For each dependency, record all of the following before opening its contextual
integration row:

| Intake evidence | Required value |
|---|---|
| Delivery | Merged PR, merge SHA, and destination repository/base branch |
| Deployment | Hosted frontend SHA, Pantheon BFF SHA, capture time, and origin |
| Contract | Accepted query keys and response-authored fulfilled identity/scope |
| State | Source health, freshness or snapshot, and honest empty/error behavior |
| Proof | Strict-live desktop and mobile capture with required-request failures |

An unmet intake row remains `waiting-for-destination`; it is not a BFF gap.

## 2. Contextual Journey Manifest

The parent owner should create one evidence record per journey. Requested and
fulfilled context must be recorded separately.

| Journey | Requested context to preserve | Fulfillment evidence | Safe behavior while absent |
|---|---|---|---|
| Cockpit -> Performance Center | persona, strategy, pool, runtime, period/as-of when supported | Destination response stable ids, applied scope, section health/time | Keep Cockpit summary contextual; label formal analysis unavailable |
| Persona Fleet -> Performance/holdings | persona and runtime binding plus supported period | Response-authored binding/link or typed destination fulfillment | Keep compact Fleet summary; do not duplicate attribution or holdings |
| Entity detail -> Performance/Rankings | stable persona/strategy/pool id, dimension, period | Matching response id and fulfilled dimension/period | Omit unsupported deep link or open visibly unscoped when legitimate |
| Human Inbox -> decision -> origin | item/target id and allow-listed return context | Review state plus a distinct durable operation receipt when applied | Use neutral return; never infer applied state from review completion |
| Capital/Rebalance/Policy detail | stable resource id and stage/context | Live detail or explicit healthy-empty/unavailable source state | No fixture, sibling-resource, inferred-policy, or false-zero substitution |
| Agora <-> Management Performance | strategy and period | Independent fulfilled scope, health, timestamp, and score meaning per surface | Keep execution performance separate from portfolio attribution |

Display names, row order, rank, metric equality, nearby timestamps, fixtures,
and client-side joins are not identity or fulfillment evidence.

## 3. First-gap Routing

Classify the first observed divergence using exactly one outcome:

- `frontend-ready`: the deployed adapter accepts the typed key and the response
  proves the fulfilled identity/scope;
- `frontend-unscoped`: the destination is legitimately unscoped and visibly
  says so;
- `unavailable`: safe composition is impossible, so the link or section stays
  unavailable;
- `split-to-bff`: deployed evidence proves one smallest missing response
  boundary that a named Pantheon BFF owner must supply.

Only `split-to-bff` creates a BFF handoff. It must name the source and
destination routes, deployed SHAs, requested and fulfilled keys, redacted
request/response evidence, the first missing stable id/link/applied-scope/
health/snapshot/receipt field, one valid case, one fail-closed negative case,
and the frontend behavior until delivery.

Do not request a generic filter expansion, aggregate endpoint, duplicate
analysis page, browser join, inferred identity, fixture authority, or new
mutation path. Independent reads retain section-local health and timestamps
unless an operator action demonstrably requires atomic semantics.

## 4. Parent Implementation Checklist

After all relevant intake gates open, the parent should:

1. map entry points to canonical destinations and centralize typed
   parse/serialize rules with per-destination allow-lists;
2. reset pagination or continuation state when entity, dimension, stage, or
   period changes, and reject arbitrary return URLs;
3. bind visible filters to response-fulfilled scope, not merely requested URL
   values;
4. preserve backend ids, links, lifecycle values, health, timestamps, and
   receipts verbatim; render missing/non-finite metrics unavailable;
5. verify direct load, refresh, copied URL, back/forward, and Inbox return in
   strict-live desktop and mobile runs without seed/fixture fallback;
6. attach every unresolved first gap to exactly one owner and bounded ticket.

## 5. Review And Absorption

Reviewer `Antigravity` should verify that this remains support-only, that the
dependency gate prevents provisional behavior from becoming contract truth,
and that every journey fails closed. As parent owner, `Antigravity` decides
what to absorb after the destinations are merged and deployed. Parent reviewer
`Claude` reviews the eventual composed implementation and hosted evidence.

Suggested transition:

```bash
AI_NAME=Codex ./scripts/ai-status.sh handoff \
  MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-20 Antigravity \
  "Support-only post-merge intake manifest and bounded BFF gap routing ready for review."
```

## 6. Preparation Evidence

- Prepared on
  `task/MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-20` from `dev`.
- Read the task-scoped brief, collaboration guide, worker-anchor and closeout
  protocols, current task/parent dependency state, and preceding handoffs.
- Used `AI_NAME=Codex` for status commands and did not scan `current-work.md`
  or the complete `ai-activity-log.jsonl`.
- Changed only this support artifact. No canonical truth, BFF/runtime code,
  registry/governance implementation, or frontend source was changed.
