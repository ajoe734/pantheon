# Backend Dev Full-Service Gap Inventory - 2026-04-29

This inventory distinguishes the current dev VM state from the current
`backend-dev-publish-20260429` branch state, and defines what must be fixed or
deployed before dev can be treated as the environment that runs every safe
Pantheon service.

## Current State

- Dev VM: `pantheon-dev-vm1`
- Dev runtime checkout: `/home/edna/code/pantheon-backend-dev-publish-20260429`
- Dev runtime deployed commit: `c9ec7d5194df4f48534c29a457fc89651f3edd8a`
- Current local/remote branch head: `e6f6d359c0e86b83f29f382c4ac2a3caa426f957`
- Local worktree before writing this inventory: clean

The dev VM is behind the branch by these commits:

- `7300648` - OpenClaw gateway adapter boundary and compose wiring
- `d2a609e` - BFF auth facade hardening
- `03036c1` - chair autonomy/orchestrator updates
- `ca3bd96` - closeout/auth automation
- `183b394` - planning materialization updates
- `4b875f6` - optional channel readyz contract docs/tests
- `a7e23e8` - dashboard archive
- `2cb3031` - closeout inventory
- `e6f6d35` - review artifact preservation

## Dev Root Compose Services Already Deployed

The deployed `c9ec7d5` root compose currently defines these services:

- infra: `minio`, `minio-init`, `nats`, `postgres`, `signal-store`
- control/runtime: `operator-bff`, `router`, `persona`, `governance`,
  `runtime-manager`, `telemetry`, `registry`, `feedback`
- domain services: `consultation-svc`, `source-ingest`, `search-svc`,
  `training-session-svc`, `policy-learning-svc`, `research-orchestrator-svc`,
  `reconciliation-drift-svc`, `research-worker-gateway-svc`
- operations/projections: `deployment`, `evaluation`, `evolution`, `incidents`,
  `postmortems`, `capital`, `promotion`, `memory`, `lineage-read`,
  `optimizer-svc`

Important correction: `policy-learning-svc` is defined in the deployed compose
but is currently exited on dev. Its logs show:

```text
ModuleNotFoundError: No module named 'services'
```

The root cause is the same Docker import-path issue fixed earlier for several
hyphenated service directories: the container starts with `uvicorn main:app
--app-dir services/policy-learning`, while the service imports
`services.foundation.health`. The Dockerfile needs:

```dockerfile
PYTHONPATH=/workspace:/workspace/services/policy-learning
```

## Root Compose Gaps To Deploy From Branch Head

### 1. `openclaw-gateway-adapter`

Branch `HEAD` adds this service to default root compose:

- service: `openclaw-gateway-adapter`
- port: `18104 -> 8104`
- healthcheck: `/livez`
- readiness: `/readyz`
- safety state: upstream-client-ready/degraded capability facade only;
  broker/paper/live/capital gates remain deferred by default
- production broker: forced disabled
- paper adapter: forced disabled

This service is not deployed on dev because dev is still on `c9ec7d5`.

Before deploying it, fix its Docker import path too. It imports
`services.foundation.health` but starts with:

```text
uvicorn main:app --app-dir services/openclaw-gateway-adapter
```

So its Dockerfile should also set:

```dockerfile
PYTHONPATH=/workspace:/workspace/services/openclaw-gateway-adapter
```

### 2. `openclaw-gateway`

`openclaw-gateway` exists in root compose but is behind the optional
`openclaw` profile:

```text
docker compose --profile openclaw ...
```

It is intentionally not a default dev service. The safe default is to deploy
the Pantheon-owned adapter and let it report `upstream_client_degraded` with a
degraded upstream envelope. If dev
needs every optional profile service too, start the profile explicitly and run
the OpenClaw smoke path separately.

The optional upstream gateway compose healthcheck uses `/readyz`. The
Pantheon-owned adapter remains a default service and uses `/livez` for its
container healthcheck so the process can stay healthy while `/readyz` correctly
returns degraded when upstream OpenClaw is absent.

### 3. BFF auth facade hardening

Branch `HEAD` contains BFF auth/RBAC hardening after the deployed `c9ec7d5`.
The targeted tests pass locally:

```text
43 passed
```

This should be included in the next dev root compose publish because the
frontend now depends on role-shaped operator tokens and stricter command gates.

## Execution / LEAN Stack Gap

LEAN is present as the `lean/` git submodule:

```text
lean -> 0ca2bdbd44c532afddaf60181e3bd7217b4ef810
```

The dev deploy checkout currently has the submodule uninitialized:

```text
-0ca2bdbd44c532afddaf60181e3bd7217b4ef810 lean
lean-config-missing
```

That means `docker-compose.exec.yml` cannot be considered deployed on dev yet.

`docker-compose.exec.yml` defines a separate compose project:

- `runtime-manager` on `28081`
- `broker-adapter` on `28097`
- `exchange-adapter` on `28098`
- `signal-store` on `26379`
- `pantheon-paper-runtime` on `28110`
- optional live profile: `pantheon-lean-live` on `28111`

For dev, only the safe paper/default stack should be started first:

```text
docker compose -p pantheon-exec -f docker-compose.exec.yml up -d --build
```

Do not start the `live` profile in dev unless explicitly approved with the
proper broker-secret boundary.

Validation blocker found locally:

```text
services/execution/lean_runtime/test_paper_runtime.py::PaperRuntimeServiceTest::test_drain_once_executes_signal_and_updates_runtime_state
failed because the fixture signal timestamp is 2026-04-18 and the stale-signal
guard now rejects signals older than 24h on 2026-04-29.
```

This is a test-fixture freshness issue, but it should be fixed before using the
exec stack as a publish gate.

## Optional Channel Services

`services/channels/web` is a FastAPI service with `/healthz`, `/livez`,
`/readyz`, `/health`, `/chat`, and `/stream/{session_id}`. Its tests pass when
run as a separate module group:

```text
9 passed
```

It is now included in the default dev compose stack through
`services/channels/web/Dockerfile`, exposed on `${WEB_CHANNEL_PORT:-18105}`, and
wired to the internal `router` service through `ROUTER_URL=http://router:8001`.
The smoke stack also waits for its `/readyz` endpoint.

`services/channels/telegram` and `services/channels/discord` are SDK bot
processes. They require real tokens and do not expose the repository-standard
HTTP health contract. They should not be default dev services until wrapped in
a Pantheon-owned HTTP supervisor or moved behind an explicit optional profile
with secret handling.

## Cron / Worker / Library-Like Areas

These paths exist but are not currently default long-running HTTP services:

- `services/control-plane/cron`
- `services/learning/*`
- `services/research/*` worker/adaptor subpackages
- `services/data-plane`
- `services/frontend`
- `services/signal-store`

They should be treated as jobs, workers, libraries, or deferred production
adapters unless a Dockerfile, health surface, port, and compose entry are added.

## Recommended Publish Sequence

1. Fix Docker import paths:
   - `services/policy-learning/Dockerfile`
   - `services/openclaw-gateway-adapter/Dockerfile`
2. Re-run local checks:
   - `docker compose config --quiet`
   - `docker compose -f docker-compose.exec.yml config --quiet`
   - OpenClaw adapter tests
   - BFF auth facade tests
   - policy-learning tests
   - channel web tests as a separate group
   - execution/LEAN tests after refreshing the stale timestamp fixture
3. Update dev root checkout from `c9ec7d5` to branch `HEAD`.
4. Rebuild root dev compose with safe env:
   - `PANTHEON_ENV=dev`
   - `PANTHEON_LIVE_BROKER_ENABLED=false`
   - `PANTHEON_BFF_CORS_ORIGINS=https://pantheon-ai-system-front-dev.lovable.app`
5. Verify root dev services:
   - every default compose service is running or completed if one-shot
   - `policy-learning-svc` stays healthy
   - `openclaw-gateway-adapter` is healthy on `/livez`
   - adapter `/readyz` returns either `200 ok` with upstream or `503 degraded`
     without crashing
   - BFF public `/health`, `/readyz`, and CORS preflight still pass
6. Initialize/update the `lean` submodule on dev.
7. Build and start the default execution stack from `docker-compose.exec.yml`.
8. Verify execution services:
   - `http://127.0.0.1:28081/__health__`
   - `http://127.0.0.1:28097/__health__`
   - `http://127.0.0.1:28098/__health__`
   - `http://127.0.0.1:28110/__health__`
9. Decide whether to add the web channel to dev compose. Telegram and Discord
   should remain deferred until token and supervisor boundaries are explicit.

## Acceptance Definition

Dev can be called "full service" only when:

- root compose default services all run healthy, including `policy-learning-svc`
  and `openclaw-gateway-adapter`
- execution compose default services run healthy with LEAN submodule initialized
- no live broker profile is enabled in dev
- optional services are either running behind explicit safe profiles or
  documented as intentionally deferred
- public BFF health/readiness/CORS remains valid for the Lovable dev origin
