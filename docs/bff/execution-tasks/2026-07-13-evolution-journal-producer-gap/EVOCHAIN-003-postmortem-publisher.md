# EVOCHAIN-003: Postmortem Publisher on Incident Resolution

Status: implemented

Task: `docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/INDEX.md`
(Wave 0, owner Antigravity, reviewer Codex)

## Scope

- Resolve incident resolve/close delivery issues where deployed incident and postmortems services did not receive `POSTMORTEMS_URL` or `EVOLUTION_URL` causing localhost defaults to fail.
- Reconcile the Postgres vs JSON IncidentStore issue by mapping postmortems directly to the `POST /api/evolution/proposals` endpoint with self-contained payloads instead of the ID-only `/api/evolution/proposals/from-postmortem-published` route.
- Add retryable and auditable at-least-once delivery mechanisms to both hops (incident -> postmortems, postmortem -> evolution).
- Add caller tests for resolve, close, publish, duplicates, and failures without no-op monkeypatches.

## Implemented Changes

1. **First Hop (Incident to Postmortem)**:
   - Upgraded `_publish_to_postmortems_if_resolved` in `services/incidents/main.py`.
   - Replaced simple POST with retryable at-least-once mechanism (3 attempts, 1.0s delay).
   - Added auditing logs prefixed with `AUDIT:` to record attempt number, status code, success, and failures.
   - Raises `HTTPException(502)` on final failure to ensure caller awareness for at-least-once semantics.

2. **Second Hop (Postmortem to Evolution)**:
   - Upgraded `_publish_postmortem_to_evolution_if_needed` in `services/postmortems/main.py`.
   - Used `build_published_postmortem_proposal_request` from `services.evolution.postmortem_bridge` to build the complete, self-contained proposal request payload (fully resolved `ProposeRequest`).
   - Delivered directly to `POST /api/evolution/proposals` (avoiding the ID-only route that reads the separate local `incidents.json` store).
   - Added retryable at-least-once mechanism (3 attempts, 1.0s delay) and detailed `AUDIT:` logs.
   - Raises `HTTPException(502)` on final failure.

3. **Verification Tests**:
   - Added `services/postmortems/test_evochain_003_delivery.py` and `services/incidents/test_evochain_003_delivery.py`.
   - Verified successful deliveries, duplicate responses, and retries/failures without using no-op stub monkeypatches (using `unittest.mock` to mock only `httpx.post`).

## Verification Results

Both unit test suites run cleanly:

```sh
python3 -m pytest services/postmortems/test_evochain_003_delivery.py services/incidents/test_evochain_003_delivery.py -v
```

Output:
```text
services/postmortems/test_evochain_003_delivery.py::test_publish_delivery_success PASSED [ 16%]
services/postmortems/test_evochain_003_delivery.py::test_publish_delivery_duplicate_success PASSED [ 33%]
services/postmortems/test_evochain_003_delivery.py::test_publish_delivery_failure_retry_and_error PASSED [ 50%]
services/incidents/test_evochain_003_delivery.py::test_incident_resolution_delivery_success PASSED [ 66%]
services/incidents/test_incidents_close_delivery_success PASSED [ 83%]
services/incidents/test_incident_delivery_failure_retry_and_error PASSED [100%]
```

Full incident & postmortems test suite (79 tests) also passes cleanly:
```sh
python3 -m pytest services/postmortems/ services/incidents/ -v
```
All passed.

## Residual Risks and Out of Scope

- The daily sweep scheduler activation (`EVOCHAIN-002`) and telemetry breach sweep (`EVOCHAIN-001`) are independent tasks that will populate the database from the producer side.
- Risk: If the network is partition-blocked for a duration longer than 3 retries, the transition status call returns 502, requiring operator manual retry or replay.
- Expiry: Re-verify during `EVOCHAIN-010` (producer-chain live verifier) end-to-end integration test.
