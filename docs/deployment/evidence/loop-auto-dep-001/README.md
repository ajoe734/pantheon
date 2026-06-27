# LOOP-AUTO-DEP-001 Evidence

Task: `LOOP-AUTO-DEP-001`
Owner: `Claude`
Reviewer: `Codex`
Wave: Wave 3 Deployment Saga

## Scope

Add durable deployment saga outbox consumer so that approved DeploymentPlan
transitions advance without manual endpoint stepping.

## Delivered Artifacts

- `services/deployment/outbox_consumer_worker.py` — durable poll-based outbox consumer
- `services/deployment/test_outbox_consumer_worker.py` — 19 unit tests
- `docker-compose.yml` — `deployment-outbox-consumer` service stanza (profile-gated)

## Acceptance Criteria Coverage

| Criterion | How satisfied |
|---|---|
| Deployment outbox events are consumed durably | Worker polls `GET /api/deployment/outbox?status=pending` each tick and POSTs `consume` for each event. `restart: unless-stopped` ensures the worker survives crashes. |
| Duplicate outbox events are idempotent | The underlying `DeploymentSagaStore.consume_event` deduplicates via inbox receipt. Worker classifies `status=duplicate` receipts as non-errors and counts them separately. |
| Consumer exposes health, last success, last failure | Worker emits a structured JSON line per tick including `health.status`, `health.last_success`, `health.last_failure`, `health.last_failure_reason`. Optional `DEPLOYMENT_OUTBOX_CONSUMER_HEALTH_FILE` env var writes health to a persistent file. |

## Validation

Run on 2026-06-27:

```bash
pytest services/deployment/test_outbox_consumer_worker.py -v
```

Result:

```
19 passed in 2.11s
```

Test classes:
- `TestFetchPendingOutbox` — polls outbox with `status=pending` filter
- `TestConsumeEvent` — applied and duplicate receipts, URL correctness
- `TestRunPoll` — 0 events, new events, duplicates, out-of-order receipts, partial errors, missing event_id
- `TestWriteHealth` — health file write and silent failure
- `TestMain` — max_ticks termination, last_success/failure health state, health file written

## Compose Profile

The worker is gated behind the `deployment-outbox-consumer` profile so it does
not start by default during normal dev stack runs. To activate:

```bash
docker compose --profile deployment-outbox-consumer up -d
```

Environment variables:

| Variable | Default | Description |
|---|---|---|
| `DEPLOYMENT_API_URL` | `http://deployment:8095` | Deployment service base URL |
| `DEPLOYMENT_OUTBOX_CONSUMER_NAME` | `deployment-outbox-consumer` | Consumer identity written to inbox receipts |
| `DEPLOYMENT_OUTBOX_CONSUMER_INTERVAL_SECONDS` | `10` | Poll interval in seconds |
| `DEPLOYMENT_OUTBOX_CONSUMER_MAX_TICKS` | `0` (unlimited) | Stop after N ticks (0 = run forever) |
| `DEPLOYMENT_OUTBOX_CONSUMER_HEALTH_FILE` | `` (disabled) | Write health JSON to this path each tick |

## Maturity Claim

This task moves `promotion_deployment` loop from `api-only` to `reconciled`:
the outbox is now durably consumed by a supervised worker rather than requiring
manual HTTP calls. The idempotent consume path and health observability are
verified by unit tests and the evidence above.
