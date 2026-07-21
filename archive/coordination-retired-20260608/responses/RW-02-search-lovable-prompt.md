Build the `RW-02-search` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/RW-02-search-bff-gap.yaml` using `.coordination/requests/RW-02-search-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `research-search`.
Workbench: `research-workbench`.
Screen ID: `screen-research-search`.
Allowed endpoints:
- GET /api/v1/research/search
Published Pantheon dependencies:
- .coordination/responses/RW-02-search-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- fetch search results from GET /api/v1/research/search only
- send q, match_type, status, date_range, page_token, and page_size exactly as published
- render result rows from the published SearchResult object only
- preserve backend ordering and relevance_score; do not re-rank client-side
- navigate drilldowns through links.result_detail and links.linked_ticket_detail only
- render degradation and corpus-freshness copy from meta.surfaces.search_results and meta.index_adapter.*
- emit a bff-gap handoff if any required field is absent
Required feedback bundle:
- docs/pantheon-feedback/RW-02-search/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/RW-02-search/API_GAP_REQUESTS.json
- docs/pantheon-feedback/RW-02-search/UI_DECISIONS.md
- docs/pantheon-feedback/RW-02-search/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/RW-02-search-ui-done.yaml` using `.coordination/requests/RW-02-search-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/RW-02-search-frontend-feedback.yaml` using `.coordination/requests/RW-02-search-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/RW-02-search.md
- docs/pantheon-handoffs/RW-02-search/FRONTEND_CHANGE_SPEC.md
- docs/bff/RW-02-search.md
- docs/pantheon-handoffs/RW-02-search
- docs/examples/RW-02-search.json
