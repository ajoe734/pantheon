# Round 2 — Results

**Executed:** 2026-06-14 (UTC). **Target:** dev BFF
`https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io` (live OpenAPI ground
truth, 447 paths).

## Breadth sweep

196 parameter-free GET paths probed with stub auth. Status distribution:

| Status | Count | Meaning |
|---|---|---|
| 200 | 187 | reachable, healthy |
| 422 | 6 | reachable; requires query params (validation), expected |
| 410 | 2 | intentionally retired |
| 404 | 1 | **ghost route — defect** |

- **H1 (auth breadth): PASS.** Sampled protected routes (`control-room`,
  `strategies`, `me`, `deployment-plans`, `ooda/packets`, `audit`) all return
  401 without a token.
- **H3 (deprecation honesty): PASS.** The two 410s — `/bff/ranking/formulas`,
  `/bff/mcp/servers` — are intentionally-retired endpoints.
- 422s (`artifacts/compare`, `lineage/graph`, `research/search`,
  `trainer/replay`, `trainer/sessions`, `quarterly-ranking/drilldown`) are
  reachable handlers that require query parameters — expected, not defects.

## Findings

### F3 — `/api/v1/incidents/stream` ghost route: SSE stream shadowed by `{incident_id}` (FIXED this branch)

`/api/v1/incidents/stream` is published in the OpenAPI but returns **404**
live, with body `"Incident not found" / details.reason="Incident stream does
not exist"`.

Root cause: in `services/control-plane/bff/main.py`,
`@app.get("/api/v1/incidents/{incident_id}")` (was line 17927) is registered
**before** `@app.get("/api/v1/incidents/stream")` (was line 41572). FastAPI/
Starlette match in registration order, so `/incidents/stream` binds
`incident_id="stream"` and falls into `get_incident`, which 404s — the
dedicated SSE stream handler `stream_incident_events` was **dead/unreachable**.
The error detail `f"Incident {incident_id} does not exist"` produced the exact
live "Incident stream does not exist" string, confirming the path.

Impact: the incident-events SSE stream (BFF_API_CONTRACT §11.2; consumed by the
FE per `services/frontend/test_sse_live.py`) could never connect — operators
get no live incident push.

Fix: relocate the static `stream_incident_events` route to register
immediately before the parameterized `{incident_id}` route. Helper symbols
(`_handle_sse_stream`, `_incident_events`, `_incident_subscribers`) resolve at
call time, so the earlier registration is safe. Verified: the first route that
matches `/api/v1/incidents/stream` is now `stream_incident_events`;
`/api/v1/incidents/{id}` still resolves to `get_incident`.

Regression test: `services/control-plane/bff/test_incident_stream_route_order.py`
(2 tests, asserts non-shadowing + that the detail route still resolves).

### F4 — docs-site references stale allocation endpoints (drift, documented)

`docs-site` references `/api/v1/allocation/proposals` and
`/api/v1/allocation/artifacts`, which are **absent from the live OpenAPI**. The
live contract exposes artifacts under `/api/v1/artifacts*` and allocation under
`/bff/management/strategy-allocation`. This is stale/aspirational documentation
drift, not a live-system defect. Recorded for a future docs-regen round; no
code change made (the live contract is the authority here).

## Net

H1/H3 PASS. H2: one ghost route found (F3) and fixed. H4: one drift item (F4)
documented. The published contract surface is otherwise faithfully reachable
(187/196 healthy GETs, remainder explained).
