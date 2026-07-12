# MGMT-PERF-IA-006 BFF And Frontend Handoff Packet

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-006` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF` |
| Sidecar owner / reviewer | `Codex` / `Antigravity` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-12` |
| Mutates canonical | `false` |

This support-only packet inventories existing Pantheon BFF reads and gives the
parent an integration boundary. It does not define a wire contract, change L1
truth, edit Pantheon runtime or `execute-plans`, authorize a write, or approve
the parent task.

## 1. Absorbable Integration Surface

| Originating context | Existing BFF read | Parent handoff |
|---|---|---|
| Cockpit / trading pulse | `GET /bff/management/trading-pulse` and `GET /bff/management/trading-pulse/rankings` | Cards may link to the canonical Performance or Rankings center. Preserve stable entity identifiers, stage, period, and returned snapshot context; do not copy the ranking block into another formal analysis page. |
| Persona Fleet | `GET /bff/management/persona-fleet` | Fleet may retain compact operational and performance summaries, then deep-link persona/runtime/period context to the formal center. Preserve backend identifiers and source state. |
| Persona detail | `GET /api/v1/personas/{persona_id}` plus persona-scoped reads | Render identity/runtime context and a compact summary only. Formal attribution belongs at Performance Center with the persona filter preserved. |
| Formal performance | `GET /bff/management/performance-attribution`, its `by-persona`, `by-strategy`, and `by-pool` variants, and `GET /bff/management/portfolio-book` | These remain the formal attribution, exposure, holdings, and incident reads. Returned metadata, pagination, ordering, and source surfaces remain authoritative. |
| Rankings | `GET /bff/management/quarterly-ranking` and `GET .../drilldown` | Link ranking evidence to Rankings Center rather than reproducing a formal ranking table in entity details. |
| Human Inbox | `GET /bff/management/human-inbox` and `GET .../{item_id}` | Carry explicit originating route and compatible entity/period identifiers through the decision journey. Treat the inbox item as governance context, not performance truth. |
| Capital pool detail | `GET /api/v1/capital-pools/{pool_id}` and Portfolio Book pool reads | Show backend-authored detail when healthy. If the requested pool or compatible detail projection is absent, render unavailable rather than fixture authority. |
| Agora execution performance | Existing Agora-owned reads and Trading Room routes | Keep execution-quality scope in Agora. Link strategy/period context to Management Performance for formal attribution; do not relabel execution metrics as portfolio attribution. |

No inspected route establishes an atomic snapshot across all of these reads.
When a screen composes more than one response, it must keep each response's
snapshot, source health, loading, error, and empty state independent.

## 2. Query And Identity Gap

The parent needs one frontend URL context model, but it must map only fields
supported by each endpoint. Relevant context can include `persona`, `runtime`,
`strategy`, `capital_pool`, `stage`, `period`, and `as_of`; wire adapters may
translate these to the BFF's accepted names. The returned identifiers and
period/snapshot metadata prove what was fulfilled.

The current inventory does not prove a universal backend-owned context token
or compatible identity chain spanning:

`Cockpit/Fleet/entity -> formal performance or ranking -> Human Inbox -> return`

Therefore the frontend must not join or restore records by display name,
ranking position, label, actor, timestamp, or matching text. If a source lacks
the stable identifier needed by the destination, preserve the originating URL
context for navigation but render the destination data unavailable. If parent
acceptance requires a durable cross-surface link that existing payloads do not
carry, assign a separate Pantheon BFF contract task; this packet deliberately
does not choose route or field names.

## 3. Operator Journey

1. An operator enters from Cockpit, Fleet, an entity detail, Human Inbox, or
   Agora with stable backend identifiers and period context in the URL.
2. The originating page keeps a compact, clearly labeled summary. Its deep
   link names the destination's formal scope: Performance, Rankings, or Agora
   execution performance.
3. Performance Center requests the appropriate attribution or Portfolio Book
   read. It renders the returned snapshot and source state, not merely the
   requested `period` or `as_of`.
4. Rankings Center requests backend ranking evidence and preserves applicable
   persona/runtime/strategy/pool/stage/period context. Entity pages do not
   recreate a competing ranking table.
5. Human Inbox preserves an explicit return destination and compatible context.
   Back, forward, refresh, copied links, decision completion, and cancellation
   restore the originating decision view without inventing a record join.
6. Agora keeps execution metrics in Trading Room. A Management deep link
   carries strategy and period but clearly changes analytical scope.
7. A healthy authoritative empty response renders empty. A missing, unhealthy,
   incompatible, stale, fallback, or unlinked detail renders unavailable or
   degraded with the reason visible.

## 4. Frontend Handoff Rules

- Keep route parsing and serialization in one typed adapter. Unknown query
  fields survive navigation when harmless but are sent only to endpoints that
  explicitly accept them.
- Reset and isolate page tokens whenever the effective endpoint, filters,
  dimension, period, or snapshot context changes.
- Preserve BFF identifiers and lifecycle/source values verbatim. Do not infer
  entity relationships from human-readable fields.
- Distinguish compact entity summary, formal attribution, formal ranking, and
  Agora execution performance in titles, breadcrumbs, and accessible labels.
- Format only finite numeric values. Missing and non-finite values are
  unavailable, never zero.
- Keep section-local health and timestamps visible when multiple reads are
  composed; a page-level success badge cannot hide a degraded section.
- Legacy redirects must be loop-free and retain only relevant canonical
  filters. The return target must be allow-listed rather than an arbitrary URL.
- These integrations are read/navigation behavior. Any freeze, promote,
  rebalance, allocation, or other mutation remains in governed Human Review
  and apply-receipt flows.

## 5. Parent Acceptance Checklist

- Every legitimate entry point round-trips supported entity and period context
  through refresh, copied URL, back/forward, and Human Inbox return.
- Persona and strategy detail summaries cannot be confused with formal
  attribution or ranking.
- Agora execution scope stays separate and linked to Management attribution
  with strategy/period context.
- Capital pool, rebalance, ranking-policy, and other detail panels distinguish
  healthy empty from unavailable and never render fixtures as authority.
- Requested `period`/`as_of` is not claimed as fulfilled beyond returned BFF
  evidence.
- Multi-read pages expose independent source health and snapshot times.
- No browser-side heuristic join or direct service call is introduced.
- Desktop and mobile retain identity, scope, period, source state, primary
  metric, and navigation action before secondary detail.
- Parent delivery records frontend PR/merge SHA, deployed SHA ancestry,
  authenticated BFF captures, and hosted desktop/mobile evidence.

## 6. Suggested Focused Validation

1. URL parse/serialize and per-endpoint query allow-list tests for persona,
   runtime, strategy, pool, stage, period, and as-of context.
2. Entry/return tests for Cockpit, Fleet, persona detail, strategy detail,
   Human Inbox, and Agora, including refresh and browser history.
3. Adapter tests for zero, null, non-finite, healthy empty, unavailable, stale,
   fallback, degraded, unmatched, and incompatible identity states.
4. Tests proving entity summaries do not render formal ranking/attribution and
   Agora execution metrics retain their distinct scope label.
5. Independent loading/error/snapshot tests for composed BFF reads and page
   token reset when context changes.
6. Hosted strict-live BFF desktop/mobile evidence with no required-request,
   console, lazy-chunk, fixture, or fallback failures.

Useful existing backend coverage includes
`test_bff_b3_management_cockpit.py`, `test_bff_b3_persona_fleet.py`,
`test_bff_pm12_persona_league.py`, and the Performance Center contract tests
named in the `MGMT-PERF-IA-003` sidecar.

## 7. Compose And Review Ownership

- Parent owner `Antigravity` decides what to absorb into the canonical
  `execute-plans` implementation and whether a separate BFF gap task is needed.
- The `execute-plans` owner implements routes, adapters, rendering, redirects,
  and frontend tests in that repository.
- A separately assigned Pantheon BFF owner must formalize and test any missing
  stable identity or return-context contract.
- Sidecar reviewer `Antigravity` verifies that this remains support material;
  parent reviewer `Claude` evaluates the composed parent delivery.

## 8. Verification Notes

Source inspection only. Re-read the task brief, parent execution packet, and
the `MGMT-PERF-IA-003` handoff. Confirmed the named trading-pulse, Persona
Fleet, persona detail, Portfolio Book, attribution, quarterly-ranking, Human
Inbox, and capital-pool reads are registered in
`services/control-plane/bff/main.py`. No L1 truth, runtime, registry,
governance implementation, BFF route/schema, or frontend file was changed.
`current-work.md` and the complete `ai-activity-log.jsonl` were not scanned.

## Review Record

Claude reviewed this packet against `services/control-plane/bff/main.py` and
approved it: all 9 named route claims match the cited endpoints and line
numbers exactly, and the packet does not change canonical truth, BFF
runtime/schema, route registries, governance behavior, or `execute-plans`
source. Full verification is in
`support/reviews/MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-review-claude.md`. This
approval covers only this support artifact, not the parent task's own
implementation.
