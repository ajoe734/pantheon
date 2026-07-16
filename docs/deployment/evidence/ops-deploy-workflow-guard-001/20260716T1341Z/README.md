# OPS-DEPLOY-WORKFLOW-GUARD-001 Pantheon deploy failure health evidence

Captured: 2026-07-16T13:41:00Z

This packet preserves the exact service health failure that blocked the
Pantheon proof run after the shared workflow guard changes were already
reviewed and merged.

## Related proof run

| item | value |
| --- | --- |
| repo | `ajoe734/pantheon` |
| workflow | `Pantheon Nonprod Deploy` (`269991390`) |
| run | `29500642280` |
| job | `Deploy dev under shared environment lease` (`87628428239`) |
| result | `failure` |
| failed step | `Deploy dev VM stack under lease` |
| exit | `75` |

The failed GitHub job shows:

- `pantheon-reconciliation-drift-svc-1` reached `Error`.
- `pantheon-loop-run-projector-scheduler-1` reached `Error`.
- Docker Compose reported `dependency failed to start: container pantheon-reconciliation-drift-svc-1 is unhealthy`.
- The post-failure compose snapshot showed both
  `pantheon-reconciliation-drift-svc-1` and
  `pantheon-loop-run-projector-scheduler-1` as `Up ... (unhealthy)`.
- The remote SSH wrapper returned `SystemExit: 1`; GitHub reported exit `75`.

## Live service health

Read-only Docker inspection on the dev VM at `2026-07-16T13:41:00Z` showed:

| service | state |
| --- | --- |
| `reconciliation-drift-svc` | `Up 15 minutes (unhealthy)` |
| `loop-run-projector-scheduler` | `Up 15 minutes (unhealthy)` |

`reconciliation-drift-svc` readiness is failing because `/readyz` resolves the
`evaluation_count` health metric through `store.list_evaluations()`, which
crashes while parsing its JSON map:

```text
services/foundation/health.py:107 readyz
services/reconciliation-drift/main.py:602 "evaluation_count": len(store.list_evaluations())
services/reconciliation-drift/store.py:26 payload = json.loads(text)
json.decoder.JSONDecodeError: Extra data: line 20379 column 4 (char 635016)
```

The container healthcheck confirms repeated HTTP 500 readiness failures:

```text
urllib.error.HTTPError: HTTP Error 500: Internal Server Error
```

`loop-run-projector-scheduler` had no container log output in the captured
tail. Its Docker healthcheck alternated between these failures:

```text
lifecycle projector unhealthy: last poll is stale (30.831s)
lifecycle projector unhealthy: invalid controller status: degraded
```

## Interpretation

The Pantheon proof failure is not caused by the shared workflow guard. The
shared deploy workflow remained active, the run acquired the shared dev
environment lease, and the deploy failed because the dev stack never became
healthy.

The first concrete application error is the corrupted or multi-document
`reconciliation-drift` evaluations JSON map. The loop-run projector health is
also unhealthy, but the failed deploy log reports its final dependency failure
against `reconciliation-drift-svc`.

Next action: repair the service/data-path failure through a reviewed PR, then
rerun only the Pantheon proof while preserving the no disable/cancel rule.
