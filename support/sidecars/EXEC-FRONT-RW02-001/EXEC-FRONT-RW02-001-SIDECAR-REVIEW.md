# EXEC-FRONT-RW02-001 Sidecar Review Packet

## Scope

- Sidecar task: `EXEC-FRONT-RW02-001-SIDECAR-REVIEW`
- Parent task: `EXEC-FRONT-RW02-001`
- Helper kind: `review_packet`
- Owner: `Codex2`
- Reviewer: `Claude`
- Boundary: support artifact only; no canonical/runtime changes

## Parent Status Snapshot

- Parent task status in `ai-status.json`: `review`
- Parent owner / reviewer: `Claude` / `Codex`
- Current parent disposition: implementation is materially aligned with the RW-02 contract, but the loop is still blocked and not ready for closeout
- Existing formal review packet: `.coordination/reviews/RW-02-search-review.md`
- Current ui-done record: `.coordination/requests/RW-02-search-ui-done.yaml`

## What Is Already Good

- The screen is wired to `GET /api/v1/research/search` through the shared BFF client path, matching the published handoff bundle.
- The implementation now sends only the published query parameters: `q`, `match_type`, `status`, `date_range`, `page_token`, `page_size`.
- The reviewed screen preserves backend ordering and `relevance_score`, uses backend-owned `links.result_detail` and `links.linked_ticket_detail`, and renders degradation plus corpus freshness from `meta.surfaces.search_results` and `meta.index_adapter.*`.
- Local Pantheon contract verification is present:
  - `python3 -m pytest -q services/control-plane/bff/test_rw02_search_contract.py`
  - review packet records `4 passed`
- Review notes also record sibling front verification success:
  - `npx eslint ...`
  - `npm run build`

## Blocking Facts Preventing Closure

### 1. Live runtime drift still blocks HTTP acceptance

- Published contract says the route is live:
  - `.coordination/responses/RW-02-search-contract-ready.yaml`
  - `docs/bff/RW-02-search.md`
- But the recorded review shows the active operator BFF runtime at `http://127.0.0.1:18001` is stale:
  - `/health` returns `200`
  - `GET /api/v1/research/search?q=momentum` returns `404`
  - live `openapi.json` exposes no `/api/v1/research/*` path
- Pantheon follow-up already exists at `.coordination/requests/RW-02-search-needs-runtime.yaml`.

### 2. Backend-owned drilldown hrefs still do not land on mounted owner screens

- Pantheon review records BFF-provided links such as:
  - `/research/tickets/...`
  - `/research/experiments/...`
  - `/research/artifacts/...`
- The reviewed front router currently mounts `/research` and `/research/search`, but not the owner detail routes above.
- Result: the search screen honors backend-owned links, but the current front app cannot resolve those destinations.

### 3. Evidence chain is still working-tree only, not replay-clean

- `.coordination/requests/RW-02-search-ui-done.yaml` points at `source_commit: 2820e449dc95ab4677d9a7dc61d6eb7da4363aa4`.
- The recorded review says that SHA is not a truthful immutable RW-02 publication commit for the reviewed screen plus feedback bundle.
- The reviewed request pair and feedback bundle are still described as working-tree only, so supervisor/GitHub replay cannot reconstruct this cycle from one immutable front commit.

## Reviewer Handoff

- Use `.coordination/reviews/RW-02-search-review.md` as the canonical review record for findings and evidence.
- Treat this sidecar file as a compact intake packet only.
- Recommended reviewer disposition for the parent task remains: keep `EXEC-FRONT-RW02-001` in follow-up / blocked-closeout posture until all three blocking facts are cleared.

## Finalization Record

- Reviewer approval received: `2026-04-20T14:33:25Z` by `Claude`
- Owner finalization intent: archive this packet as an approved support artifact and return parent closeout authority to `EXEC-FRONT-RW02-001`
- Final sidecar disposition: `done`

## Suggested Next Checks

1. Confirm the runtime refresh handoff remains the active Pantheon-owned blocker: `.coordination/requests/RW-02-search-needs-runtime.yaml`.
2. After runtime refresh, re-check live HTTP behavior for `GET /api/v1/research/search`.
3. Re-check whether the BFF-owned detail hrefs now resolve to mounted owner routes in the active front app.
4. Require one truthful immutable front commit containing the reviewed RW-02 file set and feedback bundle before allowing loop closure.

## Source References

- `.coordination/reviews/RW-02-search-review.md`
- `.coordination/requests/RW-02-search-ui-done.yaml`
- `.coordination/requests/RW-02-search-needs-runtime.yaml`
- `.coordination/responses/RW-02-search-contract-ready.yaml`
- `.coordination/responses/RW-02-search-lovable-ui-task.yaml`
- `docs/pantheon-handoffs/RW-02-search/FRONTEND_CHANGE_SPEC.md`
