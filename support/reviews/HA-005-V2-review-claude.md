# Review: HA-005-V2 — BFF HA Observability Spec

Reviewer: Claude
Date: 2026-05-19
Status: APPROVED

## Artifacts Reviewed

- `docs/bff/bff_ha_observability_spec.md`
- `tests/bff/test_observability_spec.py`

Cross-referenced against:
- `services/bff/ha/degraded_mode.py`
- `services/bff/ha/sla_targets.json`

## Acceptance Criteria Checklist

| Requirement | Status | Notes |
|---|---|---|
| Latency histograms | PASS | `bff_http_request_duration_seconds` (HTTP) + `bff_downstream_request_duration_seconds` (dependency) both defined as Histogram with p50/p95/p99 bucket requirement |
| Error rates | PASS | `bff_http_requests_total` (route) + `bff_downstream_errors_total` (dependency) with derived signal formulas |
| SSE connection count | PASS | `bff_sse_active_connections` Gauge by replica and channel; capacity-use derived signal references `sla_targets.json` |
| Idempotency cache hit ratio | PASS | `bff_idempotency_requests_total` with `miss_reserved`/`hit_replayed`/`conflict`/`unavailable` outcomes; formula defined |
| Audit write rate | PASS | `bff_audit_writes_total` with `written`/`replayed`/`failed`/`unavailable` outcomes |
| Degraded-mode count | PASS | `bff_degraded_mode_active` Gauge + `bff_degraded_mode_transitions_total` Counter |

## Degraded Error Code Coverage

All 7 rows from `services/bff/ha/degraded_mode.py` are present and have dedicated observability behavior defined:

| Error code | Observability behavior specified |
|---|---|
| `AUTH_UNAVAILABLE` | Page/block operator access |
| `IDEMPOTENCY_UNAVAILABLE` | Page + stop command dispatch |
| `AUDIT_UNAVAILABLE` | Page + stop command dispatch |
| `REGISTRY_GOVERNANCE_UNAVAILABLE` | Governed command group rejection |
| `RUNTIME_MANAGER_UNAVAILABLE` | Secondary control path routing |
| `TELEMETRY_UNAVAILABLE` | Stale health + block health-dependent commands |
| `SSE_FANOUT_UNAVAILABLE` | Realtime degraded + read-endpoint resync |

## Alert Rules

11 alert rules covering all critical paths:
- `BFFIdempotencyUnavailable` and `BFFAuditUnavailable` are correctly page-level
- `BFFCommandAcceptedWithoutSafetyProof` is page-level — appropriate as a safety incident trigger
- Thresholds reference `services/bff/ha/sla_targets.json` environment rows

## Dashboard Audiences

Three separate dashboards with correct audience separation:
- Operator: BFF availability, p99 latency vs SLA target, degraded-mode state, SSE, commands
- Risk Owner: Idempotency hit ratio, audit failure, health-dependent blocks, in-flight reconciliation
- Developer: Histogram detail by replica, downstream latency, SSE replay freshness, error codes

## Test Coverage

`tests/bff/test_observability_spec.py` verifies:
1. All 6 non-negotiable dashboard metrics present in spec text
2. Dashboard audience sections and SLA source reference present
3. All 7 degraded error codes, critical alert names, and fail-closed chain language present

Tests are structural/contractual verification that the spec document preserves required content — appropriate for a doc-layer spec. 15 tests passed per handoff evidence.

## Fail-Closed Rules

The spec's "Fail-Closed Observability Rules" section correctly codifies:
- `IDEMPOTENCY_UNAVAILABLE` and `AUDIT_UNAVAILABLE` are command-stop conditions, not warnings
- Command dispatch must be observable as a complete chain (auth → idempotency → audit → backend → receipt)
- SSE failure must not cause BFF to invent fresh data
- Telemetry degradation must block health-dependent commands

## Minor Observations (non-blocking)

The `degraded_key` metric label description says "one of the keys from `services/bff/ha/degraded_mode.py`". The `key` field in `degraded_mode.py` uses lowercase snake_case (e.g., `auth_oidc_jwks_unavailable`) while the `error_code` field uses SCREAMING_SNAKE_CASE (e.g., `AUTH_UNAVAILABLE`). The spec does not explicitly state which form to use for the metric label. This is an implementation decision for the HA PoC instrumentation layer; the spec correctly points to `degraded_mode.py` as the authoritative source.

## Verdict

APPROVED. The observability spec is complete, aligned with existing BFF HA artifacts, and satisfies all 6 acceptance criteria from the task brief. The fail-closed coverage is correct and the test file appropriately verifies the contractual content of the spec document.
