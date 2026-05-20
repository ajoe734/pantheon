# BFF HA Observability Spec

Status: pre-gate observability artifact for `HA-005-V2`
Source: Phase 8 BFF HA planning brief Group D, `docs/bff/bff_ha_topology.md`,
`services/bff/ha/sla_targets.json`, and `services/bff/ha/degraded_mode.py`
Scope: BFF HA metrics, traces, logs, dashboard requirements, and alert
thresholds for the production topology track

This document defines the minimum observability contract required before a BFF
HA PoC can claim that multi-replica behavior is measurable. It does not change
the current dev or staging deployment baseline, does not enable production BFF
replicas, and does not amend `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`.

## Operating Boundary

BFF observability must prove the control-plane facade is healthy without
pretending that BFF owns backend truth. Metrics, traces, logs, and dashboards
must distinguish:

- BFF facade health: request admission, response latency, route errors, command
  rejection, SSE connectivity, and replica health.
- Shared safety dependencies: idempotency store, audit handoff store, shared SSE
  fanout, OIDC/JWKS, Registry/Governance, Runtime Manager, and
  Telemetry/Incident.
- Backend-owned domain truth: deployment, runtime, registry, telemetry, audit,
  broker, and capital state remain owned by their services.

The observability stack may be Prometheus/Grafana, Cloud Monitoring, OpenTelemetry
Collector plus a managed backend, or another approved stack. The metric and
label contract below is stack-neutral.

## Common Labels

Every BFF HA metric must include these bounded labels unless the metric is a
process-level gauge where the label is not meaningful:

| Label | Required values |
|---|---|
| `service` | Always `operator-bff`. |
| `environment` | `dev`, `staging`, `production`, or approved PoC namespace. |
| `replica_id` | Stable replica or task identity. |
| `route` | Normalized route template, not raw paths with IDs. |
| `method` | HTTP method for request metrics. |
| `status_class` | `2xx`, `3xx`, `4xx`, `5xx`, or `sse`. |
| `dependency` | Bounded dependency name for downstream metrics. |
| `degraded_key` | One of the keys from `services/bff/ha/degraded_mode.py`. |
| `command_group` | Bounded command group such as `runtime_lifecycle`, `approval`, or `all`. |

High-cardinality labels are forbidden: raw user IDs, trace IDs, idempotency keys,
approval IDs, deployment IDs, runtime IDs, and SSE event IDs belong in traces or
logs, not metric labels.

## Required Metrics

| Metric | Type | Labels | Purpose |
|---|---|---|---|
| `bff_http_request_duration_seconds` | Histogram | `service`, `environment`, `replica_id`, `route`, `method`, `status_class` | Latency histograms for every BFF HTTP route. Buckets must allow p50, p95, and p99 calculation against `services/bff/ha/sla_targets.json`. |
| `bff_http_requests_total` | Counter | `service`, `environment`, `replica_id`, `route`, `method`, `status_class` | Request rate and route-level error rates. |
| `bff_downstream_request_duration_seconds` | Histogram | `service`, `environment`, `replica_id`, `dependency`, `route` | Dependency latency histograms for OIDC/JWKS, Registry/Governance, Runtime Manager, Telemetry/Incident, idempotency, audit, and SSE fanout calls. |
| `bff_downstream_errors_total` | Counter | `service`, `environment`, `replica_id`, `dependency`, `route`, `error_code` | Downstream dependency error rates and degraded source attribution. |
| `bff_sse_active_connections` | Gauge | `service`, `environment`, `replica_id`, `channel` | Current SSE connection count per replica and channel. |
| `bff_sse_reconnects_total` | Counter | `service`, `environment`, `replica_id`, `channel`, `outcome` | Reconnect rate and replay outcome tracking: `resumed`, `stale_cursor`, `fanout_unavailable`, or `denied`. |
| `bff_sse_disconnects_total` | Counter | `service`, `environment`, `replica_id`, `channel`, `reason` | Disconnect spike detection and client/server separation. |
| `bff_idempotency_requests_total` | Counter | `service`, `environment`, `replica_id`, `command_group`, `outcome` | Idempotency cache hit ratio and command dedupe safety. Outcomes: `miss_reserved`, `hit_replayed`, `conflict`, `unavailable`. |
| `bff_audit_writes_total` | Counter | `service`, `environment`, `replica_id`, `command_group`, `outcome` | Audit write rate and command audit handoff health. Outcomes: `written`, `replayed`, `failed`, `unavailable`. |
| `bff_degraded_mode_active` | Gauge | `service`, `environment`, `replica_id`, `degraded_key`, `dependency` | Current degraded-mode count by key. `1` means the replica currently exposes that degraded condition. |
| `bff_degraded_mode_transitions_total` | Counter | `service`, `environment`, `replica_id`, `degraded_key`, `from_state`, `to_state` | Degraded-mode entry and recovery count. |
| `bff_commands_total` | Counter | `service`, `environment`, `replica_id`, `command_group`, `outcome`, `error_code` | Command admission, dispatch, rejection, and fail-closed rate. |
| `bff_replica_health` | Gauge | `service`, `environment`, `replica_id` | Per-replica health for LB and failover drill dashboards. `1` healthy, `0` unhealthy. |

The six non-negotiable dashboard metrics are:

1. Route latency histograms from `bff_http_request_duration_seconds`.
2. Route and dependency error rates from `bff_http_requests_total` and
   `bff_downstream_errors_total`.
3. SSE connection count from `bff_sse_active_connections`.
4. Idempotency cache hit ratio from `bff_idempotency_requests_total`.
5. Audit write rate from `bff_audit_writes_total`.
6. Degraded-mode count from `bff_degraded_mode_active`.

## Degraded Error Coverage

The observability stack must preserve every fail-closed error code from
`services/bff/ha/degraded_mode.py` as a metric value, trace attribute, and log
field.

| Error code | Required observability behavior |
|---|---|
| `AUTH_UNAVAILABLE` | Page or block operator access when BFF cannot verify identity material. |
| `IDEMPOTENCY_UNAVAILABLE` | Page and stop BFF command dispatch before backend calls. |
| `AUDIT_UNAVAILABLE` | Page and stop BFF command dispatch before backend calls. |
| `REGISTRY_GOVERNANCE_UNAVAILABLE` | Show governance degraded state and reject governed command groups. |
| `RUNTIME_MANAGER_UNAVAILABLE` | Show runtime-control degraded state and route operators to the secondary control path. |
| `TELEMETRY_UNAVAILABLE` | Mark runtime health stale and block health-dependent commands. |
| `SSE_FANOUT_UNAVAILABLE` | Mark realtime state degraded and require read-endpoint resync. |

## Derived Signals

Dashboards and alerts must compute these derived signals:

| Signal | Formula |
|---|---|
| Route p99 latency | Histogram quantile over `bff_http_request_duration_seconds` by `environment`, `route`, and `method`. |
| Route error rate | `5xx / all` from `bff_http_requests_total` over a 5 minute window. |
| Dependency error rate | Downstream errors divided by downstream attempts by `dependency` over a 5 minute window. |
| SSE capacity use | Sum of `bff_sse_active_connections` divided by `sse_connections` target in `services/bff/ha/sla_targets.json`. |
| Idempotency hit ratio | `hit_replayed / (miss_reserved + hit_replayed + conflict)` over 5 minutes. `unavailable` is excluded from ratio math and alerts separately. |
| Audit write failure rate | `(failed + unavailable) / all` from `bff_audit_writes_total` over 5 minutes. |
| Degraded-mode count | Sum of `bff_degraded_mode_active` by `degraded_key` and `dependency`. |
| Command fail-closed rate | `outcome=rejected` by `error_code` from `bff_commands_total`. |

## Trace Requirements

Every inbound BFF request must start or continue a trace with:

- `trace_id` and `correlation_id` propagated from the inbound envelope or
  generated at the edge.
- A root span named with the normalized route template.
- Child spans for OIDC/JWKS, idempotency, audit, Registry/Governance, Runtime
  Manager, Telemetry/Incident, and SSE fanout calls.
- Span attributes for normalized route, method, status code, dependency,
  degraded key, command group, and fail-closed error code.
- No raw secrets, tokens, idempotency keys, PII, or payload bodies.

Command traces must show the ordered safety path:

1. Auth and policy admission.
2. Idempotency reserve or replay lookup.
3. Audit handoff write.
4. Owning backend dispatch.
5. Receipt return.

If idempotency or audit fails, the trace must stop before backend dispatch and
record the typed fail-closed error code.

## Log Requirements

BFF logs must be structured JSON with these fields:

| Field | Requirement |
|---|---|
| `timestamp` | RFC3339 UTC. |
| `level` | `INFO`, `WARN`, `ERROR`, or `CRITICAL`. |
| `service`, `environment`, `replica_id` | Same semantics as metric labels. |
| `trace_id`, `correlation_id`, `request_id` | Present for requests and command paths. |
| `route`, `method`, `status_code` | Present for HTTP requests. |
| `dependency`, `degraded_key`, `error_code` | Present for dependency failure or degraded responses. |
| `command_group`, `command_outcome` | Present for command paths. |
| `audit_outcome`, `idempotency_outcome` | Present for command safety handoff paths. |

Logs must redact secrets and raw tokens. Full request or response bodies are not
allowed in routine logs. Evidence packets may attach sanitized request/response
fixtures separately.

## Dashboard Requirements

### Operator Dashboard

Audience: operator and incident commander.

Required panels:

- BFF availability by replica and LB target.
- Route p99 latency compared with the environment p99 target from
  `services/bff/ha/sla_targets.json`.
- Current degraded-mode count by `degraded_key`.
- SSE active connections, reconnect outcomes, and disconnect reasons.
- Command rejection and fail-closed rate by `error_code`.
- Secondary control path banner state when Runtime Manager or emergency control
  dependencies are degraded.

### Risk Owner Dashboard

Audience: risk owner during command freeze, failover, or production-gated drill.

Required panels:

- Idempotency cache hit ratio, conflicts, and unavailable count.
- Audit write rate and audit failure rate.
- Runtime-health-dependent command blocks during telemetry degradation.
- In-flight command reconciliation count from drill evidence.
- Any command accepted without both idempotency and audit proof. This panel must
  normally be zero.

### Developer Dashboard

Audience: BFF and platform developers.

Required panels:

- Route p50/p95/p99 latency histograms by route and replica.
- Downstream latency and error rate by dependency.
- Per-replica request distribution and imbalance.
- SSE replay freshness and stale cursor outcomes.
- Degraded-mode transitions and recovery time by key.
- Top normalized error codes with links to traces and sanitized logs.

## Alert Rules

Alert thresholds use the environment row in `services/bff/ha/sla_targets.json`.

| Alert | Severity | Condition | Required action |
|---|---|---|---|
| `BFFRouteP99LatencyHigh` | warning | Route p99 exceeds the environment `p99_latency_ms` target for 5 minutes. | Check dependency latency and replica imbalance. |
| `BFFRouteP99LatencySustained` | page | Route p99 exceeds 2x the environment target for 10 minutes. | Incident commander decides degrade, rollback, or scale action. |
| `BFFErrorRateHigh` | warning | 5xx route error rate exceeds 1 percent for 5 minutes. | Inspect top route/dependency errors. |
| `BFFErrorRatePage` | page | 5xx route error rate exceeds 5 percent for 5 minutes or any safety route returns unexplained 5xx. | Freeze high-risk BFF commands until safety dependencies are known healthy. |
| `BFFSSEConnectionCapacityHigh` | warning | SSE active connections exceed 80 percent of the environment `sse_connections` target for 10 minutes. | Prepare scale-up or connection shedding. |
| `BFFSSEReconnectSpike` | warning | Reconnects or disconnects exceed 3x the 1 hour baseline for 10 minutes. | Check LB, fanout, and client retry behavior. |
| `BFFIdempotencyUnavailable` | page | Any `bff_idempotency_requests_total{outcome="unavailable"}` occurs on command paths. | Freeze BFF command dispatch; return `503 IDEMPOTENCY_UNAVAILABLE`. |
| `BFFIdempotencyConflictSpike` | warning | Conflict outcome exceeds 1 percent of idempotency attempts for 5 minutes. | Investigate duplicate or mismatched client retries. |
| `BFFAuditUnavailable` | page | Any `bff_audit_writes_total{outcome="unavailable"}` occurs on command paths. | Freeze BFF command dispatch; return `503 AUDIT_UNAVAILABLE`. |
| `BFFAuditWriteFailureHigh` | page | Audit write failure rate exceeds 0.1 percent over 5 minutes. | Stop command dispatch until audit handoff is healthy. |
| `BFFDegradedModeActive` | warning | Any `bff_degraded_mode_active` stays `1` for more than 5 minutes in dev or 2 minutes in staging/production. | Follow the degraded-mode matrix operator guidance. |
| `BFFCommandAcceptedWithoutSafetyProof` | page | A command dispatch trace lacks idempotency reserve/replay or audit write proof. | Treat as a safety incident and block BFF commands. |

## Fail-Closed Observability Rules

- `IDEMPOTENCY_UNAVAILABLE` and `AUDIT_UNAVAILABLE` are page-level alerts and
  command-stop conditions, not warning-only degraded states.
- A command dispatch must be observable as a complete chain: auth, idempotency,
  audit, backend dispatch, receipt. Missing idempotency or audit proof means the
  command path is unsafe and must be stopped before backend dispatch.
- SSE fanout failure may degrade realtime UI state, but it must not cause BFF to
  invent fresh data. The dashboard must show stale cursor and replay failures.
- Telemetry/Incident degradation must block commands that require fresh runtime
  health until the runtime operator or risk owner accepts a manual path.
- Any metric or dashboard that cannot distinguish BFF facade health from
  backend truth is insufficient for HA PoC acceptance.

## Evidence For HA PoC

The HA PoC evidence packet must include:

- Dashboard screenshots or exported JSON for the operator, risk owner, and
  developer dashboards.
- A synthetic route latency sample proving p99 panels use histogram quantiles.
- A synthetic idempotency replay sample proving cache hit ratio calculation.
- A synthetic audit write failure sample proving `BFFAuditUnavailable` pages and
  command dispatch stops.
- An SSE reconnect sample proving active connection count, reconnect outcome,
  and stale cursor handling.
- A degraded-mode sample for each key in `services/bff/ha/degraded_mode.py`.
- Trace evidence for one successful command and one fail-closed command stopped
  before backend dispatch.

These artifacts are PoC evidence only. Production cutover remains blocked until
the HA PoC, evidence review, and `HA-PROD-001-V2` human gate approve it.
