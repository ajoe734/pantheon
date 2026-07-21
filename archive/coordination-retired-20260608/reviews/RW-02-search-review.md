# RW-02 Search Review

Date: `2026-04-22`
Task: `RW-02-SEARCH-001`
Reviewer: `Codex`
Disposition: `close`

## Final Verification

- Verified the Pantheon-side handoff is closed and the sibling front repo now publishes the canonical request pair for this loop:
  - `../front-ai-trading-system/.coordination/requests/RW-02-search-ui-done.yaml`
  - `../front-ai-trading-system/.coordination/requests/RW-02-search-frontend-feedback.yaml`
  - `../front-ai-trading-system/docs/pantheon-feedback/RW-02-search/{LOVABLE_CHANGE_FEEDBACK.md,API_GAP_REQUESTS.json,UI_DECISIONS.md,QA_STATUS.md}`
- Verified `source_commit` `2820e449dc95ab4677d9a7dc61d6eb7da4363aa4` resolves in `../front-ai-trading-system` on branch `pkt-004-detail-fix`; the returned handoff truthfully declares the reviewed RW-02 implementation in the current workspace on top of that branch head via `source_commit_note`.
- Re-reviewed the RW-02 UI slice in `../front-ai-trading-system`:
  - `src/pages/research/ResearchSearch.tsx` calls `rw02SearchApi.search()` only with the published query params (`q`, `match_type`, `status`, `date_range`, `page_token`, `page_size`), validates the required `SearchResult`/`page_info`/`meta` fields before rendering, preserves backend ordering and `relevance_score`, opens rows through `links.result_detail`, uses `links.linked_ticket_detail` for ticket drilldown, suppresses authoritative empty-state claims on degraded snapshots, and suppresses result rendering on unavailable responses.
  - `src/lib/bffClient.ts` binds RW-02 to `GET /api/v1/research/search` and still accepts the documented simple `{ error, detail }` error envelope.
  - `src/App.tsx` mounts `/research/search`, `/research/tickets/:ticket_id`, and `/research/experiments/:experiment_id`, so the ticket and experiment owner links returned by RW-02 now land on routed front screens.
- Re-ran `python3 -m pytest -q services/control-plane/bff/test_rw02_search_contract.py` in `pantheon`; `4 passed`.
- Launched a workspace-backed operator-bff on `http://127.0.0.1:18001` and verified live degraded HTTP:
  - `GET /api/v1/research/search?q=momentum&page_size=3` under operator auth returned `200`.
  - The live payload preserved the published row shape for ticket, experiment, and artifact results.
  - `links.result_detail` came back as `/research/tickets/rt-20260419-007`, `/research/experiments/exp-20260419-012`, and `/research/artifacts/artifact-20260418-005`.
  - `links.linked_ticket_detail` stayed `/research/tickets/rt-20260419-007`.
  - `meta.surfaces.search_results = degraded` and `meta.index_adapter.adapter_state = degraded`.
- Launched a second workspace-backed operator-bff on `http://127.0.0.1:18002` with `PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK=false` and verified live unavailable HTTP:
  - `GET /api/v1/research/search?q=momentum` under operator auth returned `503`.
  - The response body matched the contract envelope:
    `{"error":"search_unavailable","meta":{"surfaces":{"search_results":"unavailable"}}}`.
- Re-ran the front static verification in `../front-ai-trading-system`:
  - `npx eslint src/pages/research/ResearchSearch.tsx src/pages/research/types.ts src/lib/bffClient.ts src/App.tsx src/components/AppSidebar.tsx src/components/WorkbenchBreadcrumb.tsx src/pages/health/Health.tsx`
  - `npm run build`
  - Both passed.

## Findings

None.

## Reviewer Note

RW-02 is contract-aligned and the prior runtime-refresh follow-up is complete.
`links.linked_ticket_detail` resolves through the mounted ticket detail route,
and `links.result_detail` resolves for the ticket and experiment rows returned
by the live degraded probe. The remaining unresolved owner link is the artifact
detail href (`/research/artifacts/:artifact_id`), which still belongs to its
own feature slice and is not mounted in the current front router. That does not
block RW-02 closeout because the search screen correctly renders backend-owned
hrefs verbatim instead of synthesizing local routes. External deployed browser
QA was not rerun in this closeout step.
