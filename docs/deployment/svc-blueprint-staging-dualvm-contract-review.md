# Review: SVC-BLUEPRINT-STAGING-DUALVM-CONTRACT

Reviewer: Claude
Date: 2026-05-03
Status: APPROVED

Historical note: this review captured the pre-Benjamin staging VM names and
internal IPs. Current VM/project values live in `staging-live-topology.md` and
`nonprod-ci-cd.md`.

## Scope

Review of the dev single-VM / staging dual-VM topology contract materialized by
Codex. Artifacts reviewed: docker-compose.yml, docker-compose.control.yml,
docker-compose.exec.yml, docker-compose.staging-full.yml,
docs/deployment/staging-live-topology.md, docs/deployment/nonprod-ci-cd.md,
scripts/validate_split_topology.sh, env/prod-control.env.example,
env/prod-exec.env.example.

## Acceptance Criteria Evaluation

### 1. `docker compose config` validates dev/control/exec/staging independently — PASS

Each compose file uses a distinct `x-pantheon-compose-contract` block with a
named topology, separate port ranges (1xxxx dev / 2xxxx exec / 3xxxx staging),
and dedicated env example files. The validation script
`scripts/validate_split_topology.sh` drives `docker compose config --format json`
against all four configurations and asserts required and forbidden services
without building any images.

### 2. Staging docs and env explicitly require VM1/VM2 separation — PASS

`staging-live-topology.md` documents:
- VM1 (`pantheon-taiwan`): control/BFF/telemetry/governance surfaces only
- VM2 (`pantheon-exec-vm2-20260424`): runtime-manager, execution runtimes,
  broker adapters, exchange adapters, TWS/IBKR session state
- Explicit separation rules section stating broker credentials are VM2-only
- Compose contract verification command documented

`nonprod-ci-cd.md` documents:
- Dual-VM deploy order (VM2 first, then VM1 control stack)
- Broker/exchange secrets must not go to GitHub variables, Lovable env, or VM1
- `docker-compose.staging-full.yml` must not re-add the dev `broker` sidecar

`env/prod-control.env.example`:
- Contains no BROKER_API_KEY, BROKER_API_SECRET, EXCHANGE_API_KEY,
  EXCHANGE_API_SECRET, SHIOAJI_API_KEY, SHIOAJI_SECRET_KEY, KRAKEN_*, TEJ_*
- Explicitly sets `OPENCLAW_BROKER_SIDECAR_URL=` (empty)
- `PANTHEON_RUNTIME_MANAGER_URL=http://10.140.0.5:28081` (VM2 internal)
- `PANTHEON_ENV=staging-live`, `PANTHEON_LIVE_BROKER_ENABLED=true`

`env/prod-exec.env.example`:
- Contains broker/exchange secret placeholders (correctly on VM2 only)
- `PANTHEON_TELEMETRY_URL=http://10.140.0.4:38083` (VM1 telemetry)

### 3. Default compose is labeled only as dev baseline — PASS

`docker-compose.yml` line 5:
```yaml
x-pantheon-compose-contract:
  topology: dev-single-vm-baseline
  execution_boundary: co-located-dev-only
```
Header comment states: "dev single-VM baseline for local and non-prod VM
development" and calls out that staging-live uses the split compose files.

## Contract Boundary Checks

**docker-compose.staging-full.yml (VM1 overlay):**
- Does not include: runtime-manager, broker, broker-adapter, exchange-adapter,
  pantheon-paper-runtime, pantheon-lean-live, signal-store
- Does include: consultation-svc, source-ingest, search-svc, training-session-svc,
  policy-learning-svc, research-orchestrator-svc, research-worker-gateway-svc,
  openclaw-gateway-adapter, router, web-channel, reconciliation-drift-svc
- `openclaw-gateway-adapter` has `OPENCLAW_BROKER_SIDECAR_URL: ${OPENCLAW_BROKER_SIDECAR_URL:-}`
  (empty default; broker sidecar on VM2 only)
- `reconciliation-drift-svc` overrides `depends_on` to remove runtime-manager
  dependency (correct — VM1 doesn't run runtime-manager)
- BFF environment correctly points runtime-manager URLs to `10.140.0.5:28081`

**docker-compose.exec.yml (VM2):**
- Does not include: operator-bff, persona, registry, promotion, lineage-read,
  governance, telemetry, incidents, postmortems, capital, evolution, evaluation,
  feedback, memory, optimizer-svc, deployment
- `pantheon-lean-live` is behind `profiles: ["live"]` gate — fail-closed
- Paper runtime `PANTHEON_TELEMETRY_URL` defaults empty, populated from
  prod-exec.env pointing to VM1 at `10.140.0.4:38083`

**scripts/validate_split_topology.sh:**
- Renders all four configurations via `docker compose config --format json`
- Validates required and forbidden services per topology
- Validates forbidden env keys (broker/exchange secrets absent from VM1 configs)
- Validates VM2 IP addressing for runtime-manager and telemetry cross-VM refs
- Validates BFF staging contract env (PANTHEON_ENV, PANTHEON_LIVE_BROKER_ENABLED,
  BFF_READ_SURFACE_STATE, CORS origins, PANTHEON_INTERNAL_API_URL)

## Observations (Non-Blocking)

1. **Health endpoint inconsistency in docker-compose.control.yml**: Some services
   use `/health`, some use `/__health__`, some use `/readyz`. This is a
   pre-existing gap already in scope for
   `SVC-BLUEPRINT-OBSERVABILITY-PROBE-FINALIZE`. Not a blocker for this task.

2. **`BFF_READ_SURFACE_STATE`** in `docker-compose.control.yml` is set to
   `${BFF_READ_SURFACE_STATE:-fresh}` (env-overridable with `fresh` default).
   The prod-control.env.example explicitly sets it to `fresh`. Correct.

## Decision

APPROVED. All acceptance criteria are satisfied:
- Four compose configurations render independently with dedicated env examples
- Staging topology explicitly enforces VM1/VM2 separation at the compose, env,
  and documentation layers
- Default compose is unambiguously labeled as dev single-VM baseline
- Validation script covers all required assertions

Return to owner (Codex) for finalization and commit.
