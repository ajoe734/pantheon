# MGMT-PERF-IA-006 BFF Handoff Follow-up 15

| Field | Value |
|---|---|
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-15` |
| Parent task | `MGMT-PERF-IA-006` — Contextual integration |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar owner / reviewer | `Codex` / `Antigravity` |
| Date | `2026-07-12` |
| Mutates canonical truth | `false` |

This support-only packet gives the parent owner a dependency-aware BFF and
frontend handoff gate. It does not add routes or fields, change canonical
semantics, modify Pantheon runtime or `execute-plans`, approve Wave 1, or
constitute acceptance of the parent task.

## 1. Current Readiness Boundary

The parent is not ready to claim integration completion. At preparation time:

- `MGMT-PERF-IA-003` is blocked pending merge of `execute-plans` PR #261 and
  hosted-dev evidence.
- `MGMT-PERF-IA-005` is `review_approved` but still pending merge of
  `execute-plans` PR #260.
- `MGMT-PERF-IA-006` remains `todo` and depends on IA-003, IA-004, and IA-005.

These are coordination observations, not new product truth. The parent should
absorb this packet only after the Wave 1 destinations are merged and their
deployed route/query behavior can be inspected. Until then, route names,
filter support, and return behavior inferred from unmerged source are not a
hosted contract.

## 2. Start Gate For Parent Integration

Before changing contextual entry points, record all of the following:

1. The merged `execute-plans` SHAs for IA-003, IA-004, and IA-005 and proof
   that the hosted dev bundle descends from them.
2. The canonical Performance and Rankings destination URLs actually exposed
   by that bundle, including their typed query allow-lists.
3. Authenticated strict-live captures for the destination BFF reads, including
   returned entity identity, fulfilled filters, source health, and snapshot or
   as-of metadata when supplied.
4. The existing Cockpit, Persona Fleet, entity-detail, Human Inbox, and Agora
   source URLs and the stable identifiers each source can legitimately carry.

If any destination is not merged/deployed, or a source cannot supply the
stable identity required by its destination, stop that integration row. Do not
compensate with a duplicate page, display-name join, fixture, or optimistic
client state.

## 3. Query-gap Decision Matrix

| Probe | Ready to compose | Stop and split to a bounded BFF task |
|---|---|---|
| Entity identity | Destination response binds the requested persona, runtime, strategy, or pool through stable ids or response-authored links. | Identity exists only in the URL, display label, table position, or inferred relationship. |
| Period/snapshot | Response supplies fulfilled period/as-of or an authoritative snapshot timestamp/source state. | Requested time context is not acknowledged and the UI would have to imply it was honored. |
| Human Inbox return | An allow-listed frontend return descriptor restores the originating entity, period, tab, and decision context. | Return requires parsing review text, accepting an arbitrary URL, or guessing the origin. |
| Governed result | Review state and any applied operation/receipt are separately retrievable. | A toast or HTTP success would be the only evidence that a mutation applied. |
| Capital Pool/Rebalance/Policy detail | Live response is renderable, including explicit healthy-empty or unavailable states. | Client fixtures, list-row expansion, or synthesized policy would become detail authority. |
| Agora cross-link | Strategy/period can be carried while Agora execution and Management attribution keep independent labels and timestamps. | A combined score or silently aligned snapshot would be required. |

No aggregate endpoint is requested by this sidecar. A split task must name the
missing stable identity, fulfilled-filter, snapshot, return, or receipt
contract and assign a Pantheon BFF owner; it must not be hidden inside the
frontend integration PR.

## 4. Operator Journey To Prove

1. Begin at a Cockpit card or alert and open the canonical destination. Record
   requested versus response-fulfilled entity and period context.
2. From one Persona Fleet row, exercise performance, holdings, ranking,
   evidence, and review links. Refresh and browser history must retain only
   supported context without turning the Fleet summary into formal analysis.
3. Open Persona and Strategy details. Their contextual summaries must remain
   visually distinct from formal attribution, and their deep links must not
   silently reset supported identity or time filters.
4. Open Capital Pool, Rebalance, and Ranking Policy details. Capture a live
   result and an empty/unavailable result; neither may fall back to fixture or
   sibling-row authority.
5. Enter Human Inbox, complete or cancel the available review journey, then
   return. The origin must restore entity, period/snapshot, tab, and decision
   context; absent operation receipt remains safely non-applied.
6. Cross-link Agora Strategy Performance and Management Performance. Strategy
   and period may carry across, but execution quality and formal attribution
   remain separate scopes with independent source state.
7. Repeat the essential path on mobile and capture final URLs, BFF failures,
   console errors, and the first context value lost.

## 5. Frontend Handoff Rules

- Centralize parsing and serialization in a typed context adapter. Preserve
  harmless unknown URL fields for navigation, but send only endpoint-supported
  query parameters.
- Treat requested context and fulfilled response context as different values.
  Never present a requested filter as fulfilled without BFF evidence.
- Reset pagination/token state when destination, entity, dimension, period, or
  snapshot changes.
- Keep loading, healthy-empty, partial, stale, degraded, unavailable,
  unauthorized, malformed, and transport-failure states section-local on
  composed pages.
- Preserve backend identifiers, links, lifecycle values, source health, and
  timestamps verbatim. Missing or non-finite metrics render unavailable, not
  zero.
- Keep return targets allow-listed and redirects loop-free. Contextual reads
  do not authorize freeze, promote, rebalance, allocation, or other writes.

## 6. Parent Evidence Checklist

- Wave 1 merge SHAs and hosted deployed-SHA ancestry are recorded.
- Every legitimate entry point round-trips supported identity and period
  context through refresh, copied URLs, back/forward, and Inbox return.
- Entity summaries cannot be mistaken for formal Performance or Rankings.
- Agora execution scope stays separate and explicitly linked.
- Empty detail contracts render honest unavailable/empty states without
  fixture authority.
- Multi-read pages expose independent response health and snapshot evidence.
- No direct service call, browser-side heuristic join, or new mutation path is
  introduced.
- Desktop and mobile strict-live captures include network and console evidence.

## 7. Review And Absorption

Reviewer `Antigravity` should verify that this packet remains support-only,
that its readiness gate reflects the parent dependencies, and that every gap
has a fail-closed stop/split disposition. Parent owner `Antigravity` decides
what to absorb after Wave 1 is deployed; parent reviewer `Claude` evaluates the
composed implementation and hosted evidence.

Suggested review transition:

```bash
AI_NAME=Antigravity ./scripts/ai-status.sh approve \
  MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-15 \
  "Dependency-aware support-only BFF/frontend handoff approved for parent absorption."
```

## 8. Preparation Evidence

- Prepared on
  `task/MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-15` from `dev`.
- Read the task-scoped brief, collaboration guide, worker-anchor and closeout
  protocols, parent execution packet, and existing MGMT-PERF-IA-006 sidecars.
- Inspected only task/parent records in `ai-status.json`; did not scan
  `current-work.md` or the complete `ai-activity-log.jsonl`.
- Changed only this support artifact. No canonical truth, BFF runtime/schema,
  registry, governance implementation, or frontend source was changed.
