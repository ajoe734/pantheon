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

For metrics judged against an approved artifact baseline (`expected_drawdown` in
`services/evolution/config/threshold_sweep_baselines.json`),
`reconciliation-drift` is the single producer of the governed drawdown `1.25x`
incident (`EVOLUTION_REVIEW_AND_THRESHOLDS.md` section 7.1, read from
`services/evolution/config/threshold_sweep_thresholds.json`
`rolling_drawdown_multiple` without copying the constant):

- Below `1.25x` (including `[1.2x, 1.25x)` warning checks), evaluation stays
  observe-only and no `DriftReport` or `IncidentCase` is opened.
- In `[1.25x, 1.5x)`, the scheduler builds a governed `DriftReport`
  (`severity=medium`) carrying the threshold fields so the `IncidentCase`
  `evidence_summary` drives an Evolution daily-sweep `RETRAIN` proposal at `LOW`
  risk.
- At or above `1.5x` (`relative_delta >= 0.5`), the `DriftReport` remains
  `critical` and enters the existing critical `FREEZE` path.
- Every baseline-judged `DriftReport` cites the metric's non-derived causal
  `{metric}_event_id` (such as `drawdown_event_id`) as its single
  `telemetry_event_id`; if the summary has no metric event ID for that metric,
  no report is built and a heartbeat is never cited.
- Evolution's `threshold_sweep_worker` neither admits a derived echo event nor
  posts a duplicate incident for a metric owned by `reconciliation-drift`
  through an approved baseline, while `GET /api/evolution/input-coverage`
  continues to report the artifact evaluable.

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

## Durable windows, SLA, and storage authority

The scheduler derives one deterministic window ID from tenant plus the
configured observation interval. The service atomically leases that window
before reading telemetry. A concurrent scheduler receives `status=deferred`;
an ambiguous transport retry uses the same window, and a completed window is
served from its durable receipt without repeating incident dispatch.

Configure the contract with:

```text
RECONCILIATION_DRIFT_SCHEDULER_WINDOW_SECONDS=300
RECONCILIATION_DRIFT_SCHEDULED_SLA_SECONDS=60
RECONCILIATION_DRIFT_SCHEDULER_TIMEOUT_SECONDS=90
RECONCILIATION_DRIFT_SCHEDULER_LEASE_SECONDS=180
RECONCILIATION_DRIFT_CONSUMER_LEASE_SECONDS=120
```

The scheduler rejects a timeout shorter than its SLA. Every response reports
`duration_seconds`, `sla_seconds`, `within_sla`, and `sla_status`. Failed or
expired claims can be recovered; an old lease token cannot complete work after
another worker owns it.

With `RECONCILIATION_DRIFT_STORE_BACKEND=postgres`, all service-owned
evaluations, alerts, ReconciliationRecords, DriftReports, logical-window
claims, and worker checkpoints use Postgres owner tables. JSON mode uses
atomic replace, fsync, and cross-process locking for the same records. Tenant
identity is part of the storage key, so two tenants may safely use the same
external record/report ID.

Consumer retry/DLQ state is atomically checkpointed around delivery attempts.
Two consumer processes sharing the state authority cannot both hold its lease.
Malformed, truncated, duplicate-key, non-standard-constant, or unsupported
state fails closed and is never overwritten as an empty state.

## Tenant authentication

Hosted/internal deployments enable tenant authentication with:

```text
RECONCILIATION_DRIFT_AUTH_MODE=token
RECONCILIATION_DRIFT_AUTH_TOKEN=<secret reference value>
PANTHEON_TENANT_ID=<tenant>
```

All `/api/reconciliation-drift/*` calls then require a matching bearer token
and `X-Tenant-Id`. Tenant identity carried by telemetry, correlation
envelopes, scheduled summaries, or incident triggers must match the
authenticated tenant. Reads are tenant-filtered, and cross-tenant record IDs
resolve only inside the authenticated scope. `disabled` is the explicit local
compatibility mode; production persistence does not implicitly disable tenant
authentication.

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
