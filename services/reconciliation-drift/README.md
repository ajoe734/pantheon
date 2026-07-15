# Reconciliation Drift Service

`reconciliation-drift-svc` is a derived read-model service. It reads telemetry-shaped evidence and writes reconciliation records, alert handoffs, and DriftReports without joining the emergency control chain.

## Default runtime telemetry chain

Compose starts the service, telemetry consumer, scheduler, and incident listener
without profiles. The consumer polls the telemetry-owned runtime-summary
projection; fixture input is never a default source. It persists pending,
completed, and dead-letter delivery state under the shared
`reconciliation-drift-data` volume and reports source lag, backlog lag, retry,
and controller status on every tick.

The scheduler independently reconciles every runtime summary against
authoritative identity, state, health, actual metrics, and queue/event delivery
lag. Missing actual-state evidence is degraded rather than green. Warning or
critical checks create a deterministic DriftReport and submit it to the
Incidents owner; stable tick IDs and persisted evaluation delivery state make
transport retries idempotent.

Generated DriftReports are readable at:

```text
GET /api/reconciliation-drift/drift-reports
GET /api/reconciliation-drift/drift-reports/{drift_report_id}
```

Set the worker interval, bounded retry count, and backoff with the
`RECONCILIATION_DRIFT_*_INTERVAL_SECONDS`, `*_MAX_ATTEMPTS`, and
`*_RETRY_BACKOFF_SECONDS` variables. Set
`RECONCILIATION_DRIFT_CONSUMER_REPLAY_DLQ=true` for an explicit consumer DLQ
replay pass. Container restart policy is `unless-stopped`, and consumer/listener
state survives process replacement.

Fixtures remain available only as an explicit local test input:

```bash
python services/reconciliation-drift/consumer.py \
  --input services/reconciliation-drift/fixtures/pnl_drift_telemetry_event.json \
  --max-ticks 1
```

## Incident Trigger Listener

On each tick it reads open incidents from:

```text
GET /api/incidents?open_only=true
```

and posts them to:

```text
POST /api/reconciliation-drift/incident-triggers/consume
```

The trigger endpoint creates a reconciliation evaluation with `trigger=incident`,
records the source `incident_id`, `source_event_id`, `telemetry_event_ids`, and
`trigger_reason`, and is idempotent by `(incident_id or source_event_id) +
binding_id`. Re-reading the same heartbeat-loss or order-rejection-spike
incident returns the existing evaluation instead of creating another record.
Failed trigger deliveries remain in the listener's atomic JSON backlog and are
replayed before newly fetched incidents after restart. Tick output exposes
attempt history, backlog count, oldest backlog age, and last success/failure.
