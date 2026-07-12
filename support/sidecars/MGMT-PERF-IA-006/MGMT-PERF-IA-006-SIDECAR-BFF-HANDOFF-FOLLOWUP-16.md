# MGMT-PERF-IA-006 BFF Handoff Follow-up 16

| Field | Value |
|---|---|
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-16` |
| Parent task | `MGMT-PERF-IA-006` — Contextual integration |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar owner / reviewer | `Codex` / `Antigravity` |
| Date | `2026-07-12` |
| Mutates canonical truth | `false` |

This support-only packet turns the parent dependency state into a row-level
BFF/frontend handoff gate. It does not add routes or fields, change canonical
semantics, modify Pantheon runtime or `execute-plans`, or approve the parent.

## 1. Dependency Snapshot And Safe Start Boundary

At preparation time, `MGMT-PERF-IA-004` is done, while the Performance Center
and Governance Decisions changes remain blocked on human merges of
`execute-plans` PRs #261 and #260 respectively. The parent remains `todo` and
depends on all three Wave 1 tasks.

The parent may inventory entry points and test the already deployed Rankings
destination, but must not claim end-to-end contextual integration until all
required Wave 1 commits are merged and present in the hosted dev bundle. Treat
each destination row independently: one deployed center does not make another
destination, filter, redirect, or return journey available.

## 2. Row-level Composition Gate

| Contextual row | Evidence required before implementation/proof | Fail-closed disposition |
|---|---|---|
| Cockpit to Performance | Hosted canonical URL; typed query allow-list; strict-live response binding persona/strategy/pool and period or snapshot | Keep the source card operational but disable or label the formal-analysis link unavailable; never infer identity from its title. |
| Persona Fleet to Performance/holdings | Deployed Performance tabs plus stable persona/runtime links and response-fulfilled context | Retain a compact Fleet summary. Do not copy attribution or holdings into Fleet to compensate for a missing destination. |
| Fleet/entity to Rankings | Hosted Rankings Center descending from the accepted IA-004 delivery; stable entity context supported by the destination | Open Rankings unscoped or unavailable when identity cannot be bound; never filter by display name or visible rank. |
| Persona/Strategy detail to Performance | Deployed formal attribution destination and a response-authored stable entity identifier | Keep detail metrics explicitly contextual and omit the deep link when fulfillment cannot be proven. |
| Human Inbox to Governance and return | Deployed Governance destination; stable item/target identity; allow-listed origin descriptor; review state distinct from operation receipt | Preserve review state, but mark absent receipt safely non-applied and provide no guessed return target. |
| Capital Pool/Rebalance/Policy detail | Live detail response or explicit empty/unavailable response with source state | Render unavailable or healthy-empty; never promote fixtures, sibling rows, or client-derived policy to authority. |
| Agora to Management Performance | Separate deployed reads with compatible strategy/period identifiers and independent timestamps | Keep Agora execution scope in Trading Room; do not synthesize a combined score or shared snapshot. |

Requested URL context is navigation intent, not proof that a BFF response
fulfilled it. Any row lacking stable identity, fulfilled-filter, snapshot,
return, or receipt evidence must be split into a bounded Pantheon BFF task
rather than repaired with a browser-side join.

## 3. Query-gap Capture Template

For every attempted row, record:

- source route and destination route, deployed frontend SHA, and Pantheon BFF
  SHA;
- requested persona, runtime, strategy, pool, stage, period, and as-of values;
- the subset actually accepted by the destination adapter;
- response-authored stable identifiers, fulfilled scope, source health, and
  snapshot timestamp;
- section-local loading, empty, partial, stale, degraded, unavailable,
  unauthorized, malformed, and transport-failure behavior;
- result: `ready`, `unscoped`, `unavailable`, or `split-to-bff`, with the first
  missing contract field or link named.

Do not record a requested filter as fulfilled merely because it survived in
the browser URL. Do not align responses by label, table position, rank,
matching metric, or nearby timestamp.

## 4. Operator Journey Run Sheet

1. Confirm the hosted bundle descends from each required Wave 1 merge before
   exercising a dependent row.
2. From Cockpit and one Persona Fleet row, open every legitimate Performance,
   holdings, Rankings, evidence, and review destination. Record the first
   identity or period value lost.
3. Refresh, copy the URL, and use back/forward at each destination. Compare
   requested context with response-fulfilled context after every navigation.
4. Verify Persona and Strategy details remain compact summaries and cannot be
   mistaken for formal attribution or ranking.
5. Exercise Capital Pool, Rebalance, and Ranking Policy detail with a live and
   an unavailable/empty case. Confirm no fixture or false zero appears.
6. Enter Human Inbox, complete or cancel the available review flow, and return.
   Confirm origin restoration and independently verify any apply receipt.
7. Cross-link Agora and Management using strategy/period context while keeping
   execution and attribution labels, health, and timestamps separate.
8. Repeat the essential journey on mobile and capture final URLs, required
   request failures, console errors, and unavailable states.

## 5. Parent Absorption Checklist

- Record all Wave 1 merge SHAs and hosted bundle ancestry.
- Centralize typed URL parsing/serialization and per-endpoint query allow-lists.
- Reset pagination/token state when destination, entity, dimension, period, or
  snapshot changes.
- Preserve stable backend ids, links, lifecycle values, source state, and
  timestamps verbatim; null or non-finite metrics remain unavailable.
- Keep multi-read health and snapshots section-local.
- Keep redirects loop-free and return targets allow-listed.
- Introduce no direct service call, heuristic join, fixture authority, or new
  mutation path.
- Route each unresolved contract gap to a named BFF owner with a narrow field
  or link requirement.

## 6. Review And Ownership

Reviewer `Antigravity` should confirm the packet remains support-only, matches
the current dependency boundary, and gives every contextual row a fail-closed
disposition. Parent owner `Antigravity` decides whether and when to absorb it;
parent reviewer `Claude` evaluates the composed frontend and hosted evidence.

Suggested review transition:

```bash
AI_NAME=Antigravity ./scripts/ai-status.sh approve \
  MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-16 \
  "Row-gated support-only BFF/frontend handoff approved for parent absorption."
```

## 7. Preparation Evidence

- Prepared on
  `task/MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-16` from `dev`.
- Read the task-scoped brief, collaboration guide, worker-anchor and closeout
  protocols, parent packet, and existing MGMT-PERF-IA-006 handoffs.
- Queried only task-scoped status through `AI_NAME=Codex`; did not scan
  `current-work.md` or the complete `ai-activity-log.jsonl`.
- Changed only this support artifact. No canonical truth, BFF runtime/schema,
  registry, governance implementation, or frontend source was changed.
