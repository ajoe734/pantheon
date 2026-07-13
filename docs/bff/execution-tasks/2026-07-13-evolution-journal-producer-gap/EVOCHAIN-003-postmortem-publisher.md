# EVOCHAIN-003: Postmortem Publisher on Incident Resolution

Status: implemented

Task: `docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/INDEX.md`
(Wave 0, owner Antigravity, reviewer Codex)

## Scope

- Resolve incident resolve/close delivery issues where deployed incident and postmortems services did not receive `POSTMORTEMS_URL` or `EVOLUTION_URL` causing localhost defaults to fail.
- Reconcile the Postgres vs JSON IncidentStore issue by mapping postmortems directly to the `POST /api/evolution/proposals` endpoint with self-contained payloads instead of the ID-only `/api/evolution/proposals/from-postmortem-published` route.
- Implement durable outbox/inbox mechanism to replace simple in-memory retries for at-least-once delivery (UnifiedOutboxStore in both incidents and postmortems).
- Harden published-event deduplication in the evolution service to enforce matching on target type, target ID (artifact ID), bridge key, and incident cluster, and reject unrelated decision conflicts (HTTP 409).
- Add caller tests for resolve, close, publish, duplicates, and failures without no-op monkeypatches.

## Implemented Changes

1. **Durable Outbox Delivery (First and Second Hop)**:
   - Implemented `UnifiedOutboxStore` with Postgres/JSON storage capability in both `incidents` and `postmortems` services.
   - Added outbox processors (`process_incidents_outbox`, `process_postmortems_outbox`) running as background tasks.
   - Outbox tasks perform retryable at-least-once delivery with exponential backoff and auditing logs.
   - Synchronous FastAPI routes write to the outbox atomically, avoiding crash-induced event loss.

2. **Second Hop (Postmortem to Evolution) & Deduplication**:
   - Wired `DATABASE_URL` and store backends (`postgres`/`json`) for `evolution` service.
   - Enforced validation of target_type, target_id, postmortem_bridge_key, and incident_cluster_id when finding existing postmortem bridge decisions.
   - Return HTTP 409 Conflict if an incoming `decision_id` is already occupied by an unrelated decision.
   - Wired `POSTMORTEMS_URL` and `EVOLUTION_URL` env vars in `docker-compose.yml` and `docker-compose.control.yml`.

3. **Verification Tests**:
   - Added comprehensive outbox delivery, validation, and regression tests in `services/incidents/test_evochain_003_delivery.py`, `services/postmortems/test_evochain_003_delivery.py`, and `services/evolution/test_evolution_service.py`.

## Verification Results

Unit test suite (133 tests) passes cleanly:

```sh
python3 -m pytest services/incidents/test_evochain_003_delivery.py services/postmortems/test_evochain_003_delivery.py services/evolution/ -v
```

All 133 tests passed successfully.

## Residual Risks and Out of Scope

- If the database is completely unavailable, outbox writes will fail synchronously, preventing status transitions to ensure consistency.
- Expiry: Re-verify during `EVOCHAIN-010` end-to-end integration test.
