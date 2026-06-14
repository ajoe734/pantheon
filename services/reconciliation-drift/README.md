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
