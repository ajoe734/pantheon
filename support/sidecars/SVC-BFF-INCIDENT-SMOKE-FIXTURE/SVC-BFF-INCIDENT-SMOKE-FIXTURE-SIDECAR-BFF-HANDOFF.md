# SVC-BFF-INCIDENT-SMOKE-FIXTURE Sidecar BFF Handoff

Task: `SVC-BFF-INCIDENT-SMOKE-FIXTURE-SIDECAR-BFF-HANDOFF`
Parent: `SVC-BFF-INCIDENT-SMOKE-FIXTURE`
Owner: Codex
Reviewer: Claude
Generated: 2026-04-30T13:53:38Z

## Scope

This packet is a support-only handoff for the parent incident smoke fixture task. It does not change canonical truth, runtime behavior, registries, or governance implementation.

The packet captures the current BFF incident/postmortem query shape, smoke evidence, frontend/operator consumption notes, and review checks for the sidecar reviewer.

## Current Evidence

Focused command run:

```bash
PANTHEON_BFF_AUTH_STUB=true python3 services/control-plane/bff/smoke_test_incident.py
```

Result: `21 passed, 0 failed`.

Important runtime note from the smoke output: command submission schema checks return `202 Accepted`, but the async worker logs downstream execution failures when no protected internal API is configured. Frontend/operator handoff should treat `POST /api/v1/operator/commands` as accepted-for-processing only, then poll command status before displaying execution success.

## Fixture And Read Path

Current parent-side smoke fixture seeds a temporary service-owned `incidents.json` and disables local snapshot fallback:

- `BFF_DATA_DIR=<temp>/bff`
- `INCIDENTS_DATA_DIR=<temp>/incidents`
- `POSTMORTEMS_DATA_DIR=<temp>/incidents`
- `PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK=false`
- `BFF_READ_SURFACE_STATE=fresh`

The fixture clears these service URL/store overrides before importing the BFF:

- `PANTHEON_BFF_INCIDENT_STORE`
- `PANTHEON_BFF_POSTMORTEM_STORE`
- `PANTHEON_INCIDENTS_API_URL`
- `PANTHEON_INCIDENTS_URL`
- `PANTHEON_POSTMORTEMS_API_URL`
- `PANTHEON_POSTMORTEMS_URL`

BFF read-store routing observed in `services/control-plane/bff/read_store.py`:

| Dataset | Preferred service inputs | File-store fallback within service boundary | Snapshot fallback |
|---|---|---|---|
| `incidents` | `PANTHEON_INCIDENTS_API_URL` or `PANTHEON_INCIDENTS_URL` -> `GET /api/incidents` | `PANTHEON_BFF_INCIDENT_STORE`, then `INCIDENTS_DATA_DIR/incidents.json` or `POSTMORTEMS_DATA_DIR/incidents.json`, nested key `incidents` | Only if enabled |
| `postmortems` | `PANTHEON_POSTMORTEMS_API_URL` or `PANTHEON_POSTMORTEMS_URL` -> `GET /api/postmortems` | `PANTHEON_BFF_POSTMORTEM_STORE`, then `POSTMORTEMS_DATA_DIR/incidents.json` or `INCIDENTS_DATA_DIR/incidents.json`, nested key `postmortems` | Only if enabled |

The smoke currently verifies that a missing incident backend does not fabricate records:

- `GET /api/v1/incidents` returns `200` with `items: []`.
- `meta.surfaces.incident_list.status == "unavailable"`.
- `meta.surfaces.incident_list.source == "missing"`.
- `meta.degradation.reason` is present.
- `GET /api/v1/incidents/inc-20260410-001` returns `404`.

## BFF Query Map

| Surface | BFF endpoint | Current response shape | Frontend handling note |
|---|---|---|---|
| Incident list | `GET /api/v1/incidents` | `{ items, page_info, meta }` | Treat `meta.surfaces.incident_list.status != "ok"` as unavailable/degraded. Do not render empty-state copy as "no incidents" when the surface is unavailable. |
| Incident detail | `GET /api/v1/incidents/{incident_id}` | `{ data, meta }` raw incident record | `404` can mean nonexistent ID or unavailable backend in the current smoke path. Prefer list/composed surface state when the UI needs to distinguish those cases. |
| Postmortem list | `GET /api/v1/postmortems` | `{ data, meta: { total, staleness } }` | Current endpoint does not expose a `postmortems` surface status. Parent may need a follow-up if postmortem missing-backend honesty must match incident list behavior. |
| Postmortem detail | `GET /api/v1/postmortems/{report_id}` | `{ data, meta }`, with optional `data.linked_incident` | Current endpoint returns `404` when missing. It does not expose backend-unavailable metadata. |
| Kill-switch status | `GET /api/v1/kill-switch/status` | `{ kill_switch, allowedActions, meta }` | Admin role required. If kill-switch/action authority is unavailable, all action booleans are false and `meta.degradation` explains why. |
| Incident response composed view | `GET /api/v1/operator/incident-response/{incident_id}` | `{ data: { incident, affected_bindings, kill_switch }, allowedActions, meta }` | Use this for active-incident operator page. Current smoke asserts no `runtime_binding`, `telemetry_summary`, `rollbacks`, or `evolution_decisions` fields in this response. |
| Post-incident review composed view | `GET /api/v1/operator/post-incident-review/{incident_id}` | `{ data: { incident, postmortem, evolution_decisions, lineage_edges, telemetry_performance }, meta }` | Use this for review/analysis page. Surface statuses are present for postmortem, evolution decisions, lineage, and telemetry performance. |
| Command submission | `POST /api/v1/operator/commands` | `202` command receipt | Do not treat `202` as execution success. Poll `GET /api/v1/operator/commands/{command_id}`. |

## Operator Journey Handoff

Active incident response:

1. Operator opens incident list with `GET /api/v1/incidents?status=open,in_progress`.
2. UI checks `meta.surfaces.incident_list`.
3. Operator opens `GET /api/v1/operator/incident-response/{incident_id}`.
4. UI renders the projected incident, affected bindings, kill-switch state, and `allowedActions`.
5. If an action is submitted through `POST /api/v1/operator/commands`, UI records the command receipt and polls command status.

Post-incident review:

1. Operator opens resolved incident list with `GET /api/v1/incidents?status=resolved`.
2. UI opens `GET /api/v1/operator/post-incident-review/{incident_id}`.
3. UI renders postmortem content when `data.postmortem` is present.
4. UI treats missing/degraded `postmortem`, `lineage`, or `telemetry_performance` surfaces as incomplete evidence, not as a clean review.

Degraded incident path:

1. If incident list surface is `unavailable`, render an unavailable/unverifiable state.
2. Keep action CTAs disabled unless the composed view reports `allowedActions.status == "ok"` and individual booleans are true.
3. Use the secondary control path guidance from `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md` for kill-switch or runtime-control escalation.

## Query Gaps For Parent Owner

These are observations only; this sidecar does not patch them.

1. Postmortem list/detail do not currently carry explicit surface status or degradation metadata when the postmortem backend is missing.
2. Incident detail `404` is honest in that it does not fabricate a record, but it does not distinguish backend missing from a real not-found ID.
3. The BFF contract table still describes incident list as `{ data: [...] }`, while the implemented BFF endpoint returns `{ items, page_info, meta }`.
4. The incident response composed view is intentionally narrower than older review notes: it excludes runtime binding, telemetry summary, rollbacks, and evolution decisions.
5. Command smoke assertions cover schema acceptance only. Downstream execution success depends on configured protected internal API and command status polling.

## Suggested Reviewer Checks

For Claude:

- Confirm this packet is support-only and did not modify L1 canonical truth or runtime implementation.
- Confirm the smoke evidence and query map match the current BFF implementation.
- Decide whether parent owner Codex2 should absorb any of the listed query gaps into the canonical parent task before closure.
- If acceptable, approve the sidecar so Codex can finalize with a task-scoped commit and `done` closeout.
