# Runtime Repair Control-Mode Actions - 2026-06-06

Status: implemented as governed BFF command actions.

This packet closes the runtime-repair action catalog gap from
`CONTROL_MODE_REPAIR_EXECUTION_TASKS_2026-06-06.md`. These actions are operator
repair controls for paper runtime and telemetry recovery. They do not grant live
broker authority, capital authority, raw shell access, or direct frontend writes.

## Governance Contract

All runtime repair actions are registered in `services/control-plane/bff/action_catalog.py`
and execute through `services/control-plane/bff/command_executor.py`.

| Action | Scope | Risk | Confirmation | Success condition |
|---|---|---:|---|---|
| `RestartPaperRuntime` | Restart one paper runtime worker | high | confirm token + idempotency key | fresh runtime heartbeat |
| `RestartTelemetryBridge` | Restart telemetry bridge for one paper runtime | high | confirm token + idempotency key | fresh telemetry projection |
| `TerminateStalePaperMonitoringSession` | End one stale monitoring session | high | confirm token + idempotency key + staleness evidence | stale session terminal receipt |
| `StartPaperMonitoringSession` | Start monitoring for one paper runtime | high | confirm token + idempotency key | fresh monitoring heartbeat |
| `ProbeTelemetryIngest` | Probe ingest freshness for one paper runtime | high | confirm token + idempotency key | ingest freshness receipt |

`totalTrades == 0` is never a failure by itself. Runtime repair success is
heartbeat and projection freshness. Trade count remains business telemetry, not
liveness proof.

## Execution Boundary

The BFF command executor forwards these commands to the protected runtime-manager
or internal runtime repair API:

| Action | Protected path |
|---|---|
| `RestartPaperRuntime` | `POST /api/internal/v1/runtime-repair/paper-runtimes/{runtime_id}/restart` |
| `RestartTelemetryBridge` | `POST /api/internal/v1/runtime-repair/paper-runtimes/{runtime_id}/telemetry-bridge/restart` |
| `TerminateStalePaperMonitoringSession` | `POST /api/internal/v1/runtime-repair/monitoring-sessions/{session_id}/terminate-stale` |
| `StartPaperMonitoringSession` | `POST /api/internal/v1/runtime-repair/paper-runtimes/{runtime_id}/monitoring-sessions/start` |
| `ProbeTelemetryIngest` | `POST /api/internal/v1/runtime-repair/paper-runtimes/{runtime_id}/telemetry-ingest/probe` |

The base URL is `PANTHEON_RUNTIME_MANAGER_API_URL`, falling back to
`PANTHEON_INTERNAL_API_URL`. The executor includes `command_id`,
`confirm_token`, `idempotency_key`, `actor_id`, `trace_id`, `stage`, and
operator reason in the forwarded payload.

## Audit Receipt

Every successful dispatch returns an audit receipt with:

- `actor_id`
- `action_id`
- `target_key`
- `target_id`
- `idempotency_key`
- `stage`
- `trace_id`
- `audit_id`
- `command_id`

The response also states `live_broker_side_effects=false` and
`capital_authority_granted=false`.

## Fail-Closed Rules

- Missing `confirm_token` blocks every runtime repair action.
- Missing `runtime_id` blocks runtime-targeted actions.
- Missing `session_id` blocks stale monitoring-session termination.
- `TerminateStalePaperMonitoringSession` also requires `staleness_evidence`
  with heartbeat age or heartbeat/stale timestamp before the command is
  forwarded.
- If the runtime-manager/internal URL is not configured, the command returns the
  existing command backend unconfigured error path.

## BFF-Down Fallback

When BFF is unavailable, operators may use the same protected runtime-manager
or admin control path with the same fields listed above. The fallback must write
the same audit receipt shape and must not bypass runtime-manager stale-session
validation.
