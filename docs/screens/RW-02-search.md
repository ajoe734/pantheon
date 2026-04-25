# RW-02 Search

## Classification

- Workbench: Research Workbench
- Screen ID: `screen-research-search`
- Feature ID: `RW-02-search`
- Packet status: **route-live** — BFF route `GET /api/v1/research/search` and `meta.index_adapter.*` are confirmed live; UI work is unblocked
- Task: `RW-02-SEARCH-001`

## Contract Note

The Research Search route and index-adapter metadata are confirmed live. UI implementation may proceed against the published result shape and backend-owned filter semantics.

The UI must not build or search the corpus in client state, and it must not infer result drilldowns from local route conventions.

## User Goal

Let a researcher issue one backend-owned search query, refine the search corpus through canonical filters, inspect ranked results, and drill into the canonical detail surface for the matched entity.

## Route

Primary route:

- `/research/search`

## Readiness Gate

Pantheon has confirmed the live route returns:

1. `GET /api/v1/research/search` with `q`, `match_type`, `status`, `date_range`, pagination, and `meta.surfaces.search_results`
2. The published `SearchResult` row shape, including `links.result_detail` and `links.linked_ticket_detail`
3. `meta.index_adapter.*` so the UI can render corpus freshness truthfully

If any required field is absent from the live payload, emit a `bff-gap` handoff instead of rendering with invented corpus or drilldown state.

## Page Sections

### 1. Query Bar

- Lives on `/research/search`.
- Contains the canonical `q` input only.
- Submits to `GET /api/v1/research/search`.
- Empty search submissions must not trigger fake local filtering.

### 2. Corpus Selector and Filter Rail

- Also lives on `/research/search`.
- Filters map exactly to backend query params:
  - `match_type`
  - `status`
  - `date_range`
- `match_type=all` is the default corpus selector.
- "Open only" behavior must be implemented as `status=open`; do not create a separate undocumented query param.

### 3. Result List

- Renders rows from `GET /api/v1/research/search`.
- Each row must show:
  - `result_id`
  - `match_type`
  - `title`
  - `excerpt`
  - `linked_ticket_id`
  - `relevance_score`
- The frontend must preserve backend result order. No client-side re-ranking.

### 4. Result Drilldown

- Row click navigates to `links.result_detail`.
- A secondary ticket anchor may navigate to `links.linked_ticket_detail`.
- The frontend must not synthesize detail paths from `match_type` or `result_id`.

### 5. Pagination Rail

- Uses `page_info.next_page_token` from the BFF response.
- The next page request must repeat the active `q`, `match_type`, `status`, and `date_range` values exactly.

### 6. Corpus Freshness / Adapter Notice

- Render search freshness from:
  - `meta.surfaces.search_results`
  - `meta.index_adapter.adapter_state`
  - `meta.index_adapter.snapshot_at`
- If the adapter is stale or degraded, the UI must make partial coverage explicit.

## Degradation Handling

| Surface state | Required behavior |
|---|---|
| `meta.surfaces.search_results = "fresh"` | normal query, filter, and result rendering |
| `meta.surfaces.search_results = "stale"` | non-dismissable staleness banner; keep result list visible |
| `meta.surfaces.search_results = "degraded"` | show degradation banner; keep available rows visible; do not present empty results as authoritative |
| `meta.surfaces.search_results = "unavailable"` | suppress result list and show unavailable notice |
| `meta.index_adapter.adapter_state != "fresh"` | show corpus-freshness notice; do not claim complete corpus coverage |

## Constraints

- Use the Pantheon BFF only. No local search index, no demo dataset, no client-side corpus assembly.
- Filter vocabulary must match the published backend query params exactly.
- Drilldowns must use BFF-provided `links.*` values only.
- If any required result field, pagination field, or `meta.index_adapter` field is missing, emit a `bff-gap` handoff instead of rendering with invented state.

## Acceptance

- Query and filter state submit only the published search route and query params.
- Result rows render from the backend-owned `SearchResult` object only.
- Pagination follows `page_token` from the BFF only.
- Drilldown navigation uses `links.result_detail` and `links.linked_ticket_detail`.
- Degradation behavior follows the published `meta.surfaces.search_results` and `meta.index_adapter.adapter_state` rules.

## References

- BFF contract: `docs/bff/RW-02-search.md`
- Example payload: `docs/examples/RW-02-search.json`
- Frontend change spec: `docs/pantheon-handoffs/RW-02-search/FRONTEND_CHANGE_SPEC.md`
- Packet family: `docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md`
