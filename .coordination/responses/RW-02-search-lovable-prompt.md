Prepare the `RW-02-search` pending-BFF placeholder in `front-ai-trading-system` using only Pantheon APIs.
Do not build the production search screen until Pantheon confirms that GET `/api/v1/research/search` returns the published SearchResult field shape and includes the published `meta.index_adapter.*` metadata.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/RW-02-search-bff-gap.yaml` using `.coordination/requests/RW-02-search-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `research-search`.
Workbench: `research-workbench`.
Screen ID: `screen-research-search`.
Allowed endpoints:
- GET /api/v1/research/search
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- do not build a client-side search corpus
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- keep the route on a blocked placeholder until Pantheon confirms the search route and adapter are live
- fetch search results from GET /api/v1/research/search only
- send q, match_type, status, date_range, page_token, and page_size exactly as published
- render result rows from the published SearchResult object only
- preserve backend ordering and relevance_score; do not re-rank client-side
- navigate drilldowns through links.result_detail and links.linked_ticket_detail only
- render degradation and corpus-freshness copy from meta.surfaces.search_results and meta.index_adapter.*
- emit a bff-gap handoff if any required field is absent
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/RW-02-search-ui-done.yaml` using `.coordination/requests/RW-02-search-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/screens/RW-02-search.md
- docs/pantheon-handoffs/RW-02-search/FRONTEND_CHANGE_SPEC.md
- docs/bff/RW-02-search.md
- docs/pantheon-handoffs/RW-02-search
- docs/examples/RW-02-search.json
