# RW-02 Search Delivery Note

## Status

`followup-required`

## Summary

Pantheon re-reviewed the returned `RW-02-search` `ui-done` cycle from
`ajoe734/front-ai-trading-system` on 2026-04-20 against the published search
contract, example payload, sibling front checkout, the current Pantheon
workspace, and the active `operator-bff` runtime.

The current front working tree fixes the earlier contract mismatches, but the
loop still cannot close. Three blockers remain:

1. the running `operator-bff` runtime at `http://127.0.0.1:18001` is stale and
   does not currently serve `GET /api/v1/research/search` over live HTTP
2. the Pantheon-owned drilldown hrefs returned by the route do not resolve to
   mounted owner screens in the current front router
3. the returned front request pair plus feedback bundle are still working-tree
   only rather than replay-clean from one immutable commit

## Verified Pantheon Contract

- Read route remains `GET /api/v1/research/search`
- Query params remain `q`, `match_type`, `status`, `date_range`, `page_token`,
  and `page_size`
- Result drilldowns remain backend-owned through
  `links.result_detail` and `links.linked_ticket_detail`
- Degradation truth remains backend-owned through
  `meta.surfaces.search_results` and `meta.index_adapter.*`
- No new Pantheon endpoint, no shadow state, and no client-side search
  synthesis is authorized from this review

## Findings Requiring Follow-Up

### 1. Pantheon runtime drift still blocks live HTTP acceptance

Evidence from this review cycle:

- `python3 -m pytest -q services/control-plane/bff/test_rw02_search_contract.py`
  passed in the current workspace (`4 passed`)
- `GET http://127.0.0.1:18001/health` returned `200 OK`
- `GET http://127.0.0.1:18001/api/v1/research/search?q=momentum` returned
  `404 Not Found`
- `http://127.0.0.1:18001/openapi.json` exposed no `/api/v1/research/*` paths

Impact:

- the requested live-BFF acceptance step cannot be completed honestly against
  the currently running runtime

Pantheon action:

- tracked in `.coordination/requests/RW-02-search-needs-runtime.yaml`
- refresh or redeploy the running `operator-bff` runtime from the current
  workspace before re-running live HTTP acceptance

### 2. The backend-owned drilldown links still do not resolve to mounted owner screens

Evidence:

- the current Pantheon app returns the published drilldown hrefs through local
  `TestClient`, including:
  - `/research/tickets/rt-20260419-007`
  - `/research/experiments/exp-20260419-012`
  - `/research/artifacts/artifact-20260418-005`
- the current front router mounts:
  - `/research` -> redirect to `/research/search`
  - `/research/search`
- the current front router does not mount:
  - `/research/tickets`
  - `/research/tickets/:ticket_id`
  - `/research/experiments/:experiment_id`
  - `/research/artifacts/:artifact_id`

Impact:

- the RW-02 search page now honors `links.result_detail` and
  `links.linked_ticket_detail`, but the requested drilldown validation cannot
  close until those owner routes exist in the front app or are otherwise
  confirmed in the deployed environment

Front-end action:

- recheck the Pantheon-owned drilldown hrefs against mounted owner screens after
  the corresponding router coverage is published

### 3. The returned front publication is not replay-clean

Evidence:

- the returned front request pair now advertises:
  - `source_branch: pkt-004-detail-fix`
  - `source_commit: 2820e449dc95ab4677d9a7dc61d6eb7da4363aa4`
  - `source_commit_note: UI implementation is present in the current workspace on top of this branch head; no new git commit was created in this task.`
- `git show --stat --no-patch 2820e449dc95ab4677d9a7dc61d6eb7da4363aa4`
  identifies that SHA as `Add KW-01 frontend feedback bundle`
- `git ls-tree -r 2820e449dc95ab4677d9a7dc61d6eb7da4363aa4 -- ...` does not
  contain the canonical RW-02 request pair, feedback bundle, or reviewed screen
  file set

Impact:

- Pantheon can inspect the local sibling checkout, but the return is not yet
  replayable under `payload_path + source_commit`

Front-end action:

- republish the canonical `ui-done` and `frontend-feedback` pair plus the full
  feedback bundle from one truthful immutable commit

## Verified Positives

- the earlier query-param contract bug is fixed: the screen now omits `status`
  and `date_range` when the operator selects the unfiltered option, rather than
  sending undocumented filter values to the BFF
- `parseErrorResponse()` in the shared BFF client now recognizes the published
  simple error envelope (`{ error, detail }`), so `400 invalid_search_query`
  and `503 search_unavailable` no longer collapse into `UNKNOWN_ERROR`
- degraded search no longer presents an authoritative empty state
- the screen now renders a dedicated unavailable notice for
  `503 search_unavailable`
- the current Pantheon app returns the expected degraded RW-02 payload through
  local `TestClient`, including `links.result_detail`,
  `links.linked_ticket_detail`, and `meta.index_adapter.*`

## Pantheon-Side Outcome

- Pantheon contract: unchanged
- Pantheon route implementation in workspace: present
- Pantheon live runtime acceptance: blocked on runtime refresh
- Pantheon review anchors:
  - `.coordination/requests/RW-02-search-ui-done.yaml`
  - `.coordination/reviews/RW-02-search-review.md`
  - `.coordination/requests/RW-02-search-needs-runtime.yaml`
- Current packet state remains open and blocked for the 2026-04-20 review cycle

## Verification Performed

- Reviewed the canonical packet bundle:
  - `docs/bff/RW-02-search.md`
  - `docs/examples/RW-02-search.json`
  - `docs/screens/RW-02-search.md`
  - `docs/pantheon-handoffs/RW-02-search/FRONTEND_CHANGE_SPEC.md`
  - `.coordination/responses/RW-02-search-contract-ready.yaml`
  - `.coordination/responses/RW-02-search-lovable-ui-task.yaml`
- Reviewed the mirrored Pantheon request:
  - `.coordination/requests/RW-02-search-ui-done.yaml`
- Reviewed the current front implementation in the sibling checkout:
  - `/home/lupin/code/front-ai-trading-system/src/pages/research/ResearchSearch.tsx`
  - `/home/lupin/code/front-ai-trading-system/src/pages/research/types.ts`
  - `/home/lupin/code/front-ai-trading-system/src/lib/bffClient.ts`
  - `/home/lupin/code/front-ai-trading-system/src/App.tsx`
- Verified Pantheon-side acceptance evidence:
  - `python3 -m pytest -q services/control-plane/bff/test_rw02_search_contract.py`
  - `GET http://127.0.0.1:18001/health`
  - `GET http://127.0.0.1:18001/api/v1/research/search?q=momentum`
  - `GET http://127.0.0.1:18001/openapi.json`
  - `git -C /home/lupin/code/front-ai-trading-system show --stat --no-patch 2820e449dc95ab4677d9a7dc61d6eb7da4363aa4`
  - `git -C /home/lupin/code/front-ai-trading-system ls-tree -r --name-only 2820e449dc95ab4677d9a7dc61d6eb7da4363aa4 -- ...`

## Not Completed

- Live HTTP acceptance against a refreshed `operator-bff` runtime was not
  possible in this worker pass because the active runtime still serves
  `404 Not Found` for the RW-02 route.
- Pantheon did not modify the RW-02 contract or BFF route shape in this review.
- Pantheon cannot mark `RW-02-search` `loop-complete` until runtime drift is
  resolved, the front repo republishes a truthful immutable return, and the
  Pantheon-owned drilldown hrefs can be verified against mounted owner routes.
