# MGMT-PERF-IA-003 BFF and Frontend Handoff Packet

**Sidecar task:** `MGMT-PERF-IA-003-SIDECAR-BFF-HANDOFF`  
**Parent task:** `MGMT-PERF-IA-003`  
**Parent owner / reviewer:** Claude / Antigravity  
**Sidecar owner / reviewer:** Codex / Claude  
**Helper kind:** `bff_handoff_packet`  
**Prepared:** 2026-07-11  
**Mutates canonical truth:** no

This is a support artifact only. It inventories the current BFF behavior and
offers an implementation handoff to the parent owner. It does not change L1
truth, BFF contracts or runtime code, and it does not implement the canonical
Performance Center in `execute-plans`.

## Parent outcome and boundary

The parent owns the canonical `/management/performance` surface with Overview,
Attribution, and Exposure & Holdings tabs. It must preserve the existing
Portfolio Book and Performance Attribution behavior, shared filters, deep-link
context, source confidence, freshness, coverage, unmatched bindings, and
explicit degraded states.

The browser boundary remains Pantheon BFF only. Frontend work belongs in
`ajoe734/execute-plans`; no frontend source should be added to Pantheon. This
packet proposes no new route and authorizes no write action.

## Current BFF inventory

| Operator question | Existing BFF surface | Useful response fields |
|---|---|---|
| What are the current portfolio positions and incidents? | `GET /bff/management/portfolio-book` | rows, incidents, links, summary, pagination, `meta.surfaces` |
| How is performance attributed across arbitrary dimensions? | `GET /bff/management/performance-attribution` | `data.items`, aggregate summary, pagination, source surfaces |
| How does a persona contribute? | `GET /bff/management/performance-attribution/by-persona` | persona-grouped rows and the same source-confidence envelope |
| How does a capital pool contribute? | `GET /bff/management/performance-attribution/by-pool` | pool-grouped rows and the same source-confidence envelope |
| How does a strategy contribute? | `GET /bff/management/performance-attribution/by-strategy` | strategy-grouped rows and the same source-confidence envelope |
| What are attributed costs? | `GET /bff/management/cost-attribution` | read-only cost rows and links back to performance attribution |

The general attribution route supports dimensions from the BFF's published
dimension set: persona, strategy, pool, asset, broker, runtime, and regime.
Current common query identifiers include `personaId`, `runtimeId`,
`strategyId`, `capitalPoolId`, `sleeveId`, `artifactId`, `brokerId`, `stage`,
`period`, and `asOf`, with legacy/name aliases also accepted. Pagination uses
`page_token` and `page_size`.

The attribution envelope exposes a snapshot timestamp, per-source surfaces,
composition sources, row counts, runtime/telemetry/holding coverage, P&L,
notional, exposure, drawdown, fill rate, slippage, trades, and latest telemetry
time. The frontend should consume these fields rather than recompute confidence
or aggregate source rows locally.

## Query and composition gap matrix

These are integration gaps for the parent to handle or explicitly defer. They
are not claims that a new BFF contract has been approved.

| Concern | Current fact | Parent handoff |
|---|---|---|
| One center, multiple reads | Portfolio, attribution, and cost are separate BFF reads with independent snapshots. | Keep each response's timestamp and surface status visible. Do not imply an atomic cross-tab snapshot unless the BFF later supplies one. |
| Shared URL filters | Attribution accepts the common identifiers; Portfolio Book has its own query contract. | Define one frontend URL model, then map only supported parameters per endpoint. Preserve unsupported context in the URL rather than silently sending or dropping it. |
| Tab deep links | BFF returns links to existing management routes, not necessarily the new center/tab URL. | Add a frontend adapter that converts known legacy destinations to `/management/performance?tab=...` while retaining entity and filter context. Do not rewrite arbitrary backend links. |
| Overview aggregation | No route in this slice is declared as a canonical all-tab Performance Center aggregate. | Compose Overview from existing reads with per-section loading/error states; do not merge confidence into a single optimistic badge. |
| Attribution grouping | The general route accepts `dimension`; specialized routes fix persona, pool, or strategy. | Prefer the specialized endpoint for those stable tabs/drill-downs. Exercise the general route for asset, broker, runtime, and regime; preserve BFF ordering. |
| Pagination | Each endpoint paginates independently. | Store pagination per tab/query key. A page token from one dimension/filter set must never be reused after filters change. |
| `asOf` and period | Parameters are accepted, while returned metadata remains the authority for the actual snapshot and period. | Render returned period/snapshot values. Do not label a requested `asOf` as fulfilled unless the response proves it. |
| Missing metrics | BFF sanitization and source surfaces distinguish missing/degraded evidence from real zeroes. | Render unavailable/missing explicitly. Never coerce null, `nan`, `NaN`, or absent values to zero. |
| Fallback attribution | Persona Fleet or identity signal may exist without a formal attribution match. | Label fallback/partial evidence and exclude it from formal attribution totals. Surface unresolved joins/incidents next to the affected row. |
| Stage | `stage` is a common filter and may be paper, canary, live, or unavailable/unknown in source data. | Preserve the returned value and an explicit unknown state; never infer live from a missing stage. |
| Writes | These surfaces are read-only; attribution metadata declares `read_only_performance_attribution`. | Performance Center must not expose direct capital, promotion, freeze, rebalance, or binding mutations. Route governed decisions to Human Review. |

## Operator journey

1. The operator enters `/management/performance`; the URL is the durable source
   for tab, period, and supported entity filters.
2. Overview requests the minimum existing BFF reads needed for summary and
   positions. Each section independently shows loading, last snapshot, source
   state, and retry behavior.
3. Attribution requests a specialized or general attribution route based on
   the selected grouping. Backend ordering, totals, pagination, and confidence
   remain authoritative.
4. Selecting a row carries its persona, strategy, pool, runtime, broker, stage,
   and period context into the relevant center tab. Refresh and copied links
   reproduce the same query.
5. Exposure & Holdings preserves Portfolio Book incidents, risk diagnostics,
   unmatched bindings, and source issues; consolidation must not hide rows to
   improve coverage.
6. If a source is partial, stale, or unavailable, the affected section remains
   visible with that state. A fresh authoritative empty response is distinct
   from unavailable data.
7. Any decision requiring a write leaves this read-only center and enters the
   governed Human Review/apply-receipt workflow.

## Frontend integration notes

- Implement in `execute-plans`, using its shared BFF client and live/strict BFF
  configuration. Do not use direct service calls or mock fallback in hosted
  acceptance.
- Use a single typed filter model, but maintain an explicit allow-list mapping
  for every endpoint. Reset its page token whenever any effective query field
  changes.
- Keep tab state and filters in search parameters so redirects, refresh, back,
  forward, and copied URLs are behavior preserving.
- Keep source-state presentation adjacent to the metric/table it qualifies.
  A global page banner alone is insufficient for mixed-source responses.
- Preserve BFF-provided row identifiers and links. When adapting a legacy link
  to the center, retain its query parameters and add the destination tab.
- Format finite numbers only. Missing values receive an unavailable label and
  accessible description; zero is rendered only when the payload supplies a
  real zero.
- On narrow screens, retain identity, stage, confidence, freshness, primary
  metric, and incident state before secondary columns.

## Suggested focused validation

Parent implementation should record exact commands and evidence for:

1. unit tests for URL parse/serialize round trips and endpoint query mapping;
2. adapter tests for null/non-finite values, zero, fallback, partial, stale,
   unavailable, unmatched binding, and authoritative empty states;
3. pagination reset and isolation across tabs, dimensions, and filters;
4. legacy Portfolio Book and Performance Attribution redirects with retained
   persona/pool/strategy/stage/period context;
5. desktop and mobile flows covering Overview, Attribution, Exposure &
   Holdings, including keyboard and accessible status labels;
6. hosted live-BFF evidence that UI row counts, labels, totals, timestamps, and
   source states match captured authenticated responses;
7. zero unexpected console exceptions, failed required requests, lazy chunk
   failures, and mock/fallback data during hosted acceptance.

Useful existing backend contract coverage includes:

- `services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py`
- `services/control-plane/bff/test_bff_performance_ranking_read_model_contract.py`
- `services/control-plane/bff/test_bff_mgmt_common_filters.py`
- `services/control-plane/bff/test_bff_mgmt_ops_001_operations_read_model_contract.py`
- `services/control-plane/bff/test_bff_management_delta_routes.py`

## Parent review checklist

- [ ] The parent accepts or revises the endpoint-to-tab mapping.
- [ ] Shared filters are round-tripped and mapped per endpoint without silent
      widening or loss.
- [ ] Portfolio incidents and unmatched rows survive consolidation.
- [ ] Formal, partial/fallback, stale, unavailable, and empty states remain
      distinguishable.
- [ ] Requested period/`asOf` is not presented as fulfilled beyond returned
      BFF evidence.
- [ ] No direct write CTA or browser-to-service call is introduced.
- [ ] Legacy redirects preserve relevant context.
- [ ] Frontend PR, merge SHA, deployed SHA ancestry, authenticated API capture,
      and desktop/mobile hosted evidence are recorded by the parent task.

## Explicit non-delivery

This sidecar does not implement frontend components, change BFF queries,
declare a new aggregate endpoint, alter canonical navigation, or certify hosted
behavior. Claude, as parent owner, decides what to compose into
`MGMT-PERF-IA-003`; Antigravity remains the parent reviewer.
