# RW-02 Search — Frontend Change Spec

## Feature

- Feature ID: `RW-02-search`
- Screen ID: `screen-research-search`
- Workbench: Research Workbench
- Packet status: contract-ready — UI implementation may proceed against the live BFF route
- Task: `RW-02-SEARCH-001`

## Summary

Build the Research Workbench search surface inside `front-ai-trading-system`. This slice includes the query bar, filter rail, ranked result list, pagination, and result drilldown behavior. All search ranking, corpus assembly, filter semantics, and drilldown targets must come from the Pantheon BFF.

## Files to Create or Modify

```text
src/pages/research/ResearchSearch.tsx          — new search page
src/pages/research/types.ts                    — add RW-02 search types
src/lib/bffClient.ts                           — add RW-02 search call
```

## Readiness Gate

Pantheon has confirmed the following are live and returning the published field shape:

- `GET /api/v1/research/search`
- `meta.index_adapter.*` in the search response
- `links.result_detail` and `links.linked_ticket_detail` in every search row

Build the production page against this live route. If any required field is absent or diverges from the synced contract, emit `.coordination/requests/RW-02-search-bff-gap.yaml` instead of falling back to placeholder or mock state.

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` in component files.

### Search route

```http
GET /api/v1/research/search
```

Supported query params:

- `q`
- `match_type`
- `status`
- `date_range`
- `page_token`
- `page_size`

Required response-only fields:

- `data[].result_id`
- `data[].match_type`
- `data[].title`
- `data[].excerpt`
- `data[].linked_ticket_id`
- `data[].relevance_score`
- `data[].links.result_detail`
- `data[].links.linked_ticket_detail`
- `page_info.next_page_token`
- `meta.surfaces.search_results`
- `meta.index_adapter.snapshot_at`
- `meta.index_adapter.adapter_state`
- `meta.index_adapter.indexed_match_types[]`
- `meta.index_adapter.source_watermarks.*`

## Component Rules

### `ResearchSearch.tsx`

- Hosts the query bar, filter rail, result list, and pagination rail.
- Query submission must call the BFF search route only.
- Filter state may be local UI state, but filter vocabulary must match backend query params exactly.
- "Open only" behavior must map to `status=open`; do not invent a separate boolean flag.
- If `meta.surfaces.search_results` is `degraded` or `unavailable`, render the shared degradation banner and do not present empty results as authoritative.
- If `meta.index_adapter.adapter_state !== "fresh"`, render a corpus-freshness notice from backend metadata.

### Result list behavior

- Preserve backend ordering exactly.
- Render `excerpt` exactly as supplied by the BFF.
- Row click navigates to `links.result_detail`.
- A ticket anchor or secondary CTA may navigate to `links.linked_ticket_detail`.
- Do not construct detail URLs from `match_type`, `result_id`, or `linked_ticket_id`.

## Constraints

- Use the existing BFF client only.
- Do not add raw network calls in components.
- Do not build a client-side search index.
- Do not reuse ticket, experiment, or artifact list data as a substitute for the search route.
- Do not infer result drilldowns from local route conventions; use `links.*`.
- If any required field is missing, emit a `bff-gap` handoff instead of mocking.

## Degradation Handling

| State | Handling |
|---|---|
| `meta.surfaces.search_results = "stale"` | render non-dismissable staleness banner; keep current result list visible |
| `meta.surfaces.search_results = "degraded"` | render degradation banner; suppress authoritative empty-state claims |
| `meta.surfaces.search_results = "unavailable"` | suppress result rendering and show unavailable notice |
| `meta.index_adapter.adapter_state != "fresh"` | render corpus freshness / partial coverage notice from backend metadata |

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/RW-02-search-ui-done.yaml` using `.coordination/requests/RW-02-search-ui-done.example.yaml` as the template.

## References

- Screen spec: `docs/screens/RW-02-search.md`
- BFF contract: `docs/bff/RW-02-search.md`
- Example payload: `docs/examples/RW-02-search.json`
- Contract-ready: `.coordination/responses/RW-02-search-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/RW-02-search-lovable-ui-task.yaml`
- Packet family: `docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md`
