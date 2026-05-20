# BFF HA Failover Demo Evidence

Status: task evidence packet for `HA-010-V2`
Scope: dev-only failover demonstration from BFF replica A to replica B

This packet proves the executable failover demo surface without changing
canonical L1 policy, compose files, production load balancers, or deployment
topology. It composes with `HA-007-V2` by reusing the local multi-replica BFF
shape and narrows the scenario to a two-replica failover.

## Demo Command

```bash
PANTHEON_BFF_FAILOVER_OUTPUT_DIR=/tmp/ha-010-v2 \
  ./scripts/bff/failover_demo.sh
```

The script starts two temporary local `operator-bff` processes:

| Replica | Default URL | Role |
|---|---|---|
| `bff-a` | `http://127.0.0.1:18201` | Initial active command endpoint; terminated by the demo. |
| `bff-b` | `http://127.0.0.1:18202` | Failover endpoint; must stay healthy and replay/read committed command state. |

Both replicas share `BFF_DATA_DIR`, run with auth stub enabled, and use the
existing final command route plus command status route. The script writes
`failover-demo.json` under `PANTHEON_BFF_FAILOVER_OUTPUT_DIR`.

## Assertions

| Row | Expected result | Evidence |
|---|---|---|
| Initial health | Replica A and B return `/health` HTTP 200 before failover. | Per-replica health checks. |
| Pre-failover command | Replica A accepts a command with a stable `Idempotency-Key`. | HTTP 202 and command receipt id. |
| RTO | After A is terminated, B is ready and replays the committed command within the SLA target from `services/bff/ha/sla_targets.json`. | Observed RTO seconds, target RTO seconds, replay receipt id. |
| RPO | The command accepted by A is readable from B with the same idempotency record. | `GET /api/v1/operator/commands/{id}` includes foundation idempotency audit data; observed RPO is 0 committed commands lost. |
| Changed retry fail-closed | B rejects a changed payload using the committed idempotency key. | HTTP 409 `IDEMPOTENCY_CONFLICT`; no silent duplicate. |
| In-flight command fail-closed | A visibly fails after termination; retrying the same payload/key through B succeeds. | Transport failure/error for A, HTTP 202 receipt from B, no silent loss detected. |

## Boundaries

- No production topology change.
- No load balancer or compose change.
- No L1 canonical policy change.
- No live broker, live capital, or runtime side effects.
- This is a dev-only direct-replica failover demo, not proof of
  production-grade shared SSE fanout or arbitrary LB reconnect.

## Verification

Latest local run:

| Command | Result |
|---|---|
| `PANTHEON_BFF_PYTHON=/tmp/ha-010-v2-venv/bin/python PANTHEON_BFF_FAILOVER_BASE_PORT=19231 PANTHEON_BFF_FAILOVER_OUTPUT_DIR=/tmp/ha-010-v2-run2 ./scripts/bff/failover_demo.sh` | PASS; `failover-demo.json` status `passed`, observed RTO `0.013s` <= dev target `300s`, observed RPO `0s` <= dev target `60s`, no silent loss detected. |

Focused tests:

```bash
pytest -q tests/bff/test_failover_demo.py
```

Full demo:

```bash
PANTHEON_BFF_FAILOVER_OUTPUT_DIR=/tmp/ha-010-v2 \
  ./scripts/bff/failover_demo.sh
```
