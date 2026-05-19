# BFF Multi-Replica Dev PoC

Status: dev-only PoC artifact for `HA-007-V2`
Scope: three local BFF replicas, shared command persistence, and realtime smoke evidence

This artifact does not change the current dev, staging, or production deployment
baseline. `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` still owns the active L1
policy: production BFF HA/LB remains behind the HA re-entry and
`HA-PROD-001-V2` human gate. The PoC starts temporary local processes only; it
does not edit compose files, service manifests, load balancer configuration, or
canonical architecture policy.

## Dev Topology

The smoke harness starts exactly three `operator-bff` replicas on loopback ports:

| Replica | Default URL | State used by the smoke |
|---|---|---|
| `bff-a` | `http://127.0.0.1:18101` | Shared `BFF_DATA_DIR` command store; process-local SSE buffer |
| `bff-b` | `http://127.0.0.1:18102` | Shared `BFF_DATA_DIR` command store; process-local SSE buffer |
| `bff-c` | `http://127.0.0.1:18103` | Shared `BFF_DATA_DIR` command store; process-local SSE buffer |

All replicas run the same FastAPI app from `services/control-plane/bff/main.py`
with `PANTHEON_BFF_AUTH_STUB=true`, `PANTHEON_BFF_AUTH_MODE=permissive`, and
`PANTHEON_ENV=dev`. They share one temporary `BFF_DATA_DIR`, which is the
current dev backing store for command idempotency and command audit records.

No load balancer is introduced. The script addresses the replicas directly so
the smoke can prove which replica accepted, replayed, or read each artifact.

## Smoke Coverage

Run:

```bash
PANTHEON_BFF_MULTI_REPLICA_OUTPUT_DIR=/tmp/ha-007-v2 \
  ./scripts/bff/run_multi_replica_smoke.sh
```

The script writes `multi-replica-smoke.json` under the output directory and
exits non-zero if a required row fails.

| Row | Expected result | Evidence |
|---|---|---|
| Replica health | All three `/health` checks return HTTP 200. | Per-replica status codes. |
| Cross-replica idempotency replay | A command submitted to replica A replays from replica B with the same receipt when retried with the same `Idempotency-Key`. | Same command/receipt id from two replicas. |
| Cross-replica idempotency conflict | Replica C rejects the same idempotency key with a changed payload. | HTTP 409 and idempotency conflict error. |
| Cross-replica audit read | Replica C can read the command status and audit record created through replica A. | `GET /api/v1/operator/commands/{command_id}` includes audit and foundation idempotency data. |
| Per-replica SSE transport | Each replica can publish and stream an authenticated `approval` SSE event from its own process-local buffer. | Published event id matches the first streamed event id for each replica. |
| Cross-replica SSE replay fail-closed | An event published on replica A is not silently replayed by replica B while the replay store is process-local. | HTTP 409 `SSE_REPLAY_UNAVAILABLE`, `X-SSE-Replay-Store: in-memory`, and resync headers. |

## Current Finding

The dev PoC proves that the current BFF can run three local replicas and that
idempotency plus command audit records compose across replicas when the replicas
share `BFF_DATA_DIR`.

Realtime is intentionally narrower: current BFF SSE replay is explicitly
process-local (`X-SSE-Replay-Store: in-memory`). The PoC therefore treats
same-replica SSE transport as the passing realtime smoke and treats
cross-replica replay as a required fail-closed check. That is consistent with
the topology document: production-grade HA still requires an external SSE event
source/fanout before arbitrary replica reconnect is allowed.

## Non-Goals

- No production load balancer.
- No compose or deployment topology change.
- No L1 canonical policy change.
- No claim that shared SSE fanout or cross-replica `Last-Event-ID` replay is
  complete.
- No live broker, live capital, or runtime side effects.
