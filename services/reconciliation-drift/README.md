# Reconciliation Drift Service

`reconciliation-drift-svc` is a derived read-model service. It reads telemetry-shaped evidence and writes reconciliation records, alert handoffs, and DriftReports without joining the emergency control chain.

## Telemetry Consumer

The consumer path is intentionally opt-in for dev:

```bash
RECONCILIATION_DRIFT_CONSUMER_MAX_TICKS=1 docker compose --profile reconciliation-drift-consumer up reconciliation-drift-consumer
```

The compose profile mounts:

```text
./services/reconciliation-drift/fixtures:/fixtures/reconciliation-drift:ro
```

By default the consumer reads `/fixtures/reconciliation-drift`, posts events to `http://reconciliation-drift-svc:8102/api/reconciliation-drift/telemetry-events/consume`, and emits DriftReports that are readable at:

```text
GET /api/reconciliation-drift/drift-reports
GET /api/reconciliation-drift/drift-reports/{drift_report_id}
```

For scheduled dev runs, leave `RECONCILIATION_DRIFT_CONSUMER_MAX_TICKS=0` and set `RECONCILIATION_DRIFT_CONSUMER_INTERVAL_SECONDS` to the desired polling interval. Reposting the same fixture is idempotent by `event_id`: the generated report id is `drift-{event_id}`.

## Incident Trigger Listener

The incident listener is opt-in for dev:

```bash
RECONCILIATION_DRIFT_INCIDENT_LISTENER_MAX_TICKS=1 docker compose --profile reconciliation-drift-incident-listener up reconciliation-drift-incident-listener
```

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
