# RW-02 Search BFF Contract

## Status

**Contract published** — the Research Search route, result projection, filter semantics, and search-index adapter behavior are now the definitive implementation target for the Pantheon BFF. UI work must not start until Pantheon confirms the route is live and returning this field shape.

Task: `RW-02-SEARCH-001`

## Purpose

Provide one canonical search surface for the Research Workbench so researchers can query the research corpus, filter results using backend-owned semantics, and navigate to canonical entity detail routes without assembling or searching the corpus in the browser.

## Route

### Search research corpus

- `GET /api/v1/research/search`

Supported query params:

- `q` — required search string; empty queries are invalid in v1
- `match_type` — `"all"` | `"ticket"` | `"experiment"` | `"artifact"`
- `status` — linked research-ticket lifecycle filter; `"open"` | `"in_progress"` | `"closed"` | `"archived"`
- `date_range` — backend-owned recency window token; `"24h"` | `"7d"` | `"30d"` | `"90d"`
- `page_token`
- `page_size` — default `25`, maximum `100`

Required response fields:

- `data[]`
  - `result_id`
  - `match_type` — `"ticket"` | `"experiment"` | `"artifact"`
  - `title`
  - `excerpt`
  - `linked_ticket_id`
  - `relevance_score`
  - `links.result_detail`
  - `links.linked_ticket_detail`
- `page_info.next_page_token`
- `page_info.total`
- `meta.snapshot_at`
- `meta.surfaces.search_results` — `"fresh"` | `"stale"` | `"degraded"` | `"unavailable"`
- `meta.index_adapter.snapshot_at`
- `meta.index_adapter.adapter_state` — `"fresh"` | `"stale"` | `"degraded"` | `"unavailable"`
- `meta.index_adapter.indexed_match_types[]`
- `meta.index_adapter.source_watermarks.tickets`
- `meta.index_adapter.source_watermarks.experiments`
- `meta.index_adapter.source_watermarks.artifacts`

## SearchResult Object

Canonical read model:

```typescript
interface ResearchSearchResponse {
  data: SearchResult[];
  page_info: {
    next_page_token: string | null;
    total: number;
  };
  meta: {
    snapshot_at: string;
    surfaces: {
      search_results: "fresh" | "stale" | "degraded" | "unavailable";
    };
    index_adapter: {
      snapshot_at: string;
      adapter_state: "fresh" | "stale" | "degraded" | "unavailable";
      indexed_match_types: Array<"ticket" | "experiment" | "artifact">;
      source_watermarks: {
        tickets: string | null;
        experiments: string | null;
        artifacts: string | null;
      };
    };
  };
}

interface SearchResult {
  result_id: string;
  match_type: "ticket" | "experiment" | "artifact";
  title: string;
  excerpt: string;
  linked_ticket_id: string;
  relevance_score: number;
  links: {
    result_detail: string;
    linked_ticket_detail: string;
  };
}
```

Required invariants:

- `result_id` is the canonical identity of the matched corpus entity, not a frontend-generated row key.
- `match_type` is backend-owned and must reflect the matched entity type; the frontend must not infer type from the returned links.
- `linked_ticket_id` always points to the owning research ticket for the result, even when `match_type` is `experiment` or `artifact`.
- `excerpt` is backend-authored search context. The frontend must render it as provided and must not generate its own snippet from raw detail routes.
- `relevance_score` is backend-owned ranking output. The frontend may display it but must not sort rows client-side by a different heuristic.
- `links.result_detail` is the only drilldown target for the matched entity.
- `links.linked_ticket_detail` is the canonical ticket-detail target for the `linked_ticket_id`.

## Filter Semantics

- `q` is the only free-text search input. The frontend must not issue multiple detail-route reads to emulate search.
- `match_type=all` searches every indexed corpus type that the adapter currently exposes through `meta.index_adapter.indexed_match_types[]`.
- `status` filters by the owning Research Ticket lifecycle from `RW-01`, not by experiment-run state or artifact version state.
- `date_range` filters by the indexed entity's backend-owned update timestamp. The frontend must not convert a calendar picker into arbitrary, undocumented query params.
- Pagination is backend-owned through `page_token` and `page_size`. Do not use client-side infinite-scroll heuristics against partial local state.

## Search Index Adapter Contract

The search route is backed by a BFF-owned adapter that indexes the Research Workbench corpus. The adapter contract is part of this packet even though it is not exposed as a standalone route.

Required adapter rules:

- The adapter is the only authority that composes tickets, experiments, and artifacts into a searchable corpus.
- The adapter must index `ticket` documents from the RW-01 read model.
- The adapter may expose `experiment` and `artifact` documents only when their upstream modules publish stable identities; until then, `meta.index_adapter.indexed_match_types[]` must exclude them or mark their source watermark as `null`.
- If one or more corpus sources are stale or unavailable, `meta.index_adapter.adapter_state` must reflect that condition and `meta.surfaces.search_results` must degrade accordingly.
- The frontend must not inspect raw ticket, experiment, or artifact APIs to backfill missing search coverage.

## Error Responses

- `400` — invalid query params
  - `{ "error": "invalid_search_query", "detail": "..." }`
- `403` — caller lacks search permission
  - `{ "error": "forbidden" }`
- `503` — search adapter unavailable
  - `{ "error": "search_unavailable", "meta": { "surfaces": { "search_results": "unavailable" } } }`

## Degradation Rules

- When `meta.surfaces.search_results = "stale"`, the shared degradation substrate from `PKT-005` must show a non-dismissable staleness banner while keeping results visible.
- When `meta.surfaces.search_results = "degraded"`, the UI may show available rows but must not claim corpus completeness or display an authoritative empty state.
- When `meta.surfaces.search_results = "unavailable"`, suppress the result list and filter-dependent empty states.
- When `meta.index_adapter.adapter_state` is not `"fresh"`, the UI must treat corpus coverage as partial or stale even if the route itself still returns rows.

## Non-Goals

- The frontend must not fetch tickets, experiments, or artifacts and perform its own in-memory search.
- The frontend must not synthesize `excerpt`, `relevance_score`, `match_type`, or drilldown routes.
- This packet does not define experiment status semantics, artifact versioning semantics, or analysis aggregation. Those remain in RW-03 through RW-05.

## Relationship to Upstream and Downstream Modules

- RW-02 depends on RW-01 for stable `linked_ticket_id` identity and the `status` filter vocabulary.
- RW-03 through RW-05 may later expand the corpus through the same adapter, but they must not require frontend search rewrites once their entities become indexable.

## Example Payload

- `docs/examples/RW-02-search.json`
