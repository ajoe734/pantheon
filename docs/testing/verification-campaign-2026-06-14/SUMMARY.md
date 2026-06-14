# Verification Campaign 2026-06-14 — Summary

Ten independent verification rounds over the live Pantheon dev system and its
supporting code/state planes, each archived as a plan + results doc, each going
deeper and broader than the last. Fixes followed the normal dev workflow
(worktree off `origin/dev` → branch → test → PR → merge `dev`).

## Coverage map (deeper ↓ / broader →)

| # | Plane | Angle | Verdict |
|---|---|---|---|
| 1 | Live control plane | reachability, health, fail-closed posture | PASS + **fix F2** |
| 2 | Contract surface | documented→live conformance (196 GETs) | **fix F3** |
| 3 | Contract surface | parameterized-route robustness (139 routes) | **fix F5** |
| 4 | Write surface | malformed/type-confused input robustness (64 POSTs) | PASS |
| 5 | Streaming | SSE media type, heartbeat, replay 409 | PASS (F6 deploy-lag) |
| 6 | AuthZ | write-endpoint role-gating (136 handlers) | PASS (F7 flagged) |
| 7 | Repo tests | collection + execution health (~1,150 tests) | PASS |
| 8 | Write methods + state plane | PUT/PATCH/DELETE + archive integrity | **fix F8** |
| 9 | Route resolution | shadowing + duplicate registration (447 paths) | PASS + **guard** |
| 10 | Contract surface | live→documented (hidden routes) | PASS |

## Defects found & fixed (via dev workflow)

| ID | Severity | Defect | PR |
|---|---|---|---|
| F2 | medium | OODA control-room card reported every stage `status: ok` while its backing source was `missing` (false-green to operators) | #1540 |
| F3 | high | `/api/v1/incidents/stream` SSE route was dead — shadowed by an earlier `/incidents/{incident_id}`; the FE incident stream could never connect | #1541 |
| F5 | medium | persona-league detail 500'd on any unknown id — the 404 branch raised a non-existent `ErrorCode.OBJECT_NOT_FOUND` (`AttributeError`); + static guard for all ErrorCode refs | #1542 |
| F8 | medium | archive indexer silently dropped legacy-`id`-schema task files from the index permanently (uncounted by dashboards) | #1553 |
| F9 | low (hazard) | 4 duplicate `(method,path)` registrations; correct today but order-dependent — locked by a route-resolution guard test | #1555 |

## Findings recorded for owners (not unilaterally changed)

- **F1** — live OODA loop is empty (0 packets/loops): the known upstream
  build-gap (no signal producer / market data / real artifacts). Fleet-owned.
- **F4 / O1–O2** — generic create/get stubs accept null bodies and 200-degraded
  unknown ids; intended placeholders.
- **F6** — Round 2's `incidents/stream` fix is merged to `dev` but needs a BFF
  redeploy to go live (deploy-lag; OPS).
- **F7** — three generic create endpoints (`knowledge/notes`, `/bff/tools`,
  `evolution-programs`) gate on read role only; low severity, product decision.
- **O3** — archive `index.json` count lag (runtime-owned, self-healing).

## New regression tests added

- `test_mgmt_ooda_005_control_room_card.py` (F2 assertion)
- `test_incident_stream_route_order.py` (F3)
- `test_persona_league_detail_not_found.py` (F5 + ErrorCode static guard)
- `.orchestrator/test_task_archive_index_legacy_id.py` (F8)
- `test_route_resolution_no_shadowing.py` (F9 + shadowing guard)

## Overall posture

The Pantheon dev control plane is **reachable, healthy, auth-gated, fail-closed,
robust to bad input, uniformly enveloped, and contract-honest in both
directions**; the service test suite is green (~1,150 tests). Five real defects
were found and fixed; the remaining gaps are upstream build-work (signal/market
data) or product/ops decisions, explicitly attributed rather than silently
worked around. The empty trading loop (F1) is a build-completeness gap, not a
control-plane defect.
