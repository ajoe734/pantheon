# LOOP-AUTO-DEP-003 Evidence

Task: add deployment saga progress feedback and DLQ replay.

## Delivered Surface

- Deployment saga progress now exposes `pending`, `running`, `blocked`,
  `failed`, and `completed` via:
  - `GET /api/deployment/sagas/{saga_id}/progress`
  - `GET /api/deployment/plans/{plan_id}/saga-progress`
  - `GET /api/deployment/projections/{plan_id}`
- Outbox delivery failures are durable:
  - `POST /api/deployment/outbox/{event_id}/failure`
  - `GET /api/deployment/outbox?status=dead_lettered`
- DLQ replay is idempotent:
  - `POST /api/deployment/outbox/{event_id}/replay`
  - repeated replay of an already pending event returns `replayed=false`
    without duplicating the event or incrementing `replay_count`.
- The outbox consumer records failed delivery attempts, applies retry policy,
  skips future `next_retry_at` records, and pushes exhausted events to DLQ.
- BFF deployment read models project `saga_progress`, `blocked_reason`,
  `retry_state`, and `dlq_count` when `deployment_sagas.json` is available.

## Verification

```bash
pytest services/deployment/test_service.py services/deployment/test_outbox_consumer_worker.py services/control-plane/bff/test_read_store_deployment.py -q
```

Result: `47 passed in 15.40s`

```bash
cd services/control-plane/governance
python3 -m unittest test_deployment_saga.py
```

Result: `Ran 9 tests in 0.017s - OK`

## Safety Boundary

No live-capital execution, approval bypass, runtime-manager binding writes, or
frontend UI behavior changed in this task.
