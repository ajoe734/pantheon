# SVC-OPENCLAW-HONEST-STACK-SEMANTICS Sidecar Acceptance Packet

Task: `SVC-OPENCLAW-HONEST-STACK-SEMANTICS-SIDECAR-ACCEPTANCE`
Parent: `SVC-OPENCLAW-HONEST-STACK-SEMANTICS`
Owner: Codex
Reviewer: Codex2
Date: 2026-04-30
Scope: support-only acceptance packet and dependency map. This packet does not change canonical truth, core contracts, runtime behavior, registry behavior, or governance implementation.

## 1. Support Finding

The current repo evidence favors the honest-stack meaning:

- default OpenClaw adapter posture can be `upstream_client_degraded` when the optional upstream gateway is absent or unhealthy;
- the degraded state is acceptable only when the capability payload carries a degraded upstream envelope and fail-closed execution gates remain closed;
- `facade_only` should not be required by the full honest-stack smoke unless the parent owner intentionally chooses to normalize service and docs back to that older semantic label;
- `/livez` is the adapter process health check; `/readyz` may return `503 degraded` when upstream OpenClaw is absent.

This is a sidecar recommendation for parent absorption, not a canonical decision.

## 2. Source Context Reviewed

Task-scoped inputs:

- `AI_COLLABORATION_GUIDE.md`
- `.orchestrator/task-briefs/svc_openclaw_honest_stack_semantics_sidecar_acceptance.md`
- `.orchestrator/skills/task-closeout-finalization.md`
- `ai-status.json`
- `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/planning-session.json`

Behavior and docs inspected for the packet:

- `scripts/smoke_honest_stack.py`
- `scripts/smoke_openclaw_activation_ready_e2e.py`
- `services/openclaw-gateway-adapter/main.py`
- `docker-compose.yml`
- `docs/deployment/openclaw-activation-ready-e2e.md`
- `docs/deployment/backend-dev-full-service-gap-inventory-2026-04-29.md`
- `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/phase2-phase6-gap-inventory.md`

## 3. Acceptance Checklist For Parent

### A. Honest Activation-State Semantics

The parent patch should make one explicit semantic choice and align code, smoke, and docs to it.

Recommended choice:

- Accept `activation_state=upstream_client_degraded` when `upstream.status=degraded`.
- Accept `activation_state=upstream_client_ready` when `upstream.status=ok`.
- Reject any capability payload where activation state and upstream envelope disagree.
- Keep `fail_closed=true` in the capability payload.
- Keep `broker_execution=deferred`, `paper_adapter=deferred`, and `live_adapter=deferred` in the default compose posture.

Alternative choice if parent wants legacy wording:

- Normalize service, smoke, and docs back to a single `facade_only` label.
- Do not leave one layer expecting `facade_only` while another reports `upstream_client_degraded`.

Pass criteria:

- Full honest-stack smoke no longer fails solely because default upstream absence reports `upstream_client_degraded`.
- Smoke still fails if degraded semantics are reported without the degraded upstream envelope.
- Smoke still fails if paper, live, production broker, or capital-binding gates open unexpectedly.

### B. Health And Readiness

Expected default behavior:

- `openclaw-gateway-adapter` remains a default compose service.
- Optional upstream `openclaw-gateway` remains behind the `openclaw` profile.
- Adapter container health uses `/livez`, not `/readyz`.
- `/readyz` can return `200 ok` with healthy upstream or `503 degraded` when upstream is absent.
- `/healthz` and `/api/openclaw-adapter/capabilities` remain readable enough for operator diagnosis in degraded mode.

Pass criteria:

- Compose default stack can become healthy without starting the optional upstream OpenClaw profile.
- `smoke-stack` depends on `openclaw-gateway-adapter` service health and then validates degraded readiness explicitly.

### C. Session And Error Envelope Semantics

Default session creation through `POST /api/openclaw-adapter/sessions` should remain safe when upstream is absent.

Acceptable envelopes:

- legacy deferral: HTTP `503`, `status=deferred`, `error_code=CAPABILITY_DENIED`, `retryable=false`;
- honest upstream absence: HTTP `503`, `status=upstream_error`, `error_code=UPSTREAM_UNAVAILABLE`, `retryable=true`, `owner_plane=openclaw_runtime`, `error_layer=upstream`.

Pass criteria:

- No default session path creates a live upstream session when upstream is unavailable.
- No default session path activates paper, live, or production broker execution.
- Retryability is truthful: capability denial is not retryable; upstream unavailability is retryable.

### D. Activation-Ready E2E Regression

The task should preserve the existing activation-ready E2E semantics:

- default degraded rows prove `/livez` ok, `/readyz` fail-closed, default capabilities deferred, paper denied, live denied;
- activation rows use fake upstream/runtime-manager/broker fixtures;
- fake upstream can produce `upstream_client_ready`;
- paper simulation is enabled only inside the task-scoped smoke environment with an active fake paper RuntimeBinding;
- live order remains denied.

Pass criteria:

- `python3 scripts/smoke_openclaw_activation_ready_e2e.py` remains green.
- If compose path is touched, `docker compose --profile openclaw-activation-ready-e2e run --rm openclaw-activation-ready-e2e` remains a valid repeatable command.

### E. Documentation Alignment

Parent-owned doc updates should remove stale single-envelope claims.

Known doc risk:

- `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/phase2-phase6-gap-inventory.md` still says session creation returns non-retryable `CAPABILITY_DENIED`; current smoke and E2E docs also allow typed `UPSTREAM_UNAVAILABLE` for upstream absence.

Pass criteria:

- Docs distinguish adapter facade availability from upstream runtime availability.
- Docs do not imply OpenClaw runtime session execution, paper execution, production adapters, broker execution, or EP5 activation is complete.
- Docs preserve fail-closed default gates and optional-profile upstream boundary.

## 4. Dependency Map

```text
docker-compose.yml
  openclaw-gateway-adapter (default service)
    build: services/openclaw-gateway-adapter/Dockerfile
    env:
      OPENCLAW_GATEWAY_URL=http://openclaw-gateway:18789
      OPENCLAW_PRODUCTION_BROKER_ENABLED=false
      OPENCLAW_PAPER_ADAPTER_ENABLED=false
      OPENCLAW_LIVE_ADAPTER_ENABLED=false
      OPENCLAW_CAPITAL_BINDING_ENABLED=false
    healthcheck: /livez
    exposes:
      /livez
      /readyz
      /healthz
      /api/openclaw-adapter/upstream/status
      /api/openclaw-adapter/capabilities
      /api/openclaw-adapter/sessions

  openclaw-gateway (optional profile: openclaw)
    image: ghcr.io/openclaw/openclaw:2026.4.7
    healthcheck: /readyz
    relationship: optional upstream only

  smoke-stack (profile: smoke)
    depends_on: openclaw-gateway-adapter service_healthy
    command: python scripts/smoke_honest_stack.py
    validates:
      adapter /livez
      adapter /healthz
      adapter /readyz
      adapter capabilities
      adapter session create deferral/upstream-unavailable path
      broker paper/live gates remain closed

  openclaw-activation-ready-e2e (profile: openclaw-activation-ready-e2e)
    command: python scripts/smoke_openclaw_activation_ready_e2e.py
    uses fake:
      OpenClaw upstream
      runtime-manager
      broker sidecar
    validates:
      default fail-closed degraded posture
      fake-upstream ready posture
      paper simulation gate only under explicit test env
      live denied
```

## 5. Review Focus For Codex2

Please review this support packet for parent-task usefulness, especially:

- whether the parent task should absorb `upstream_client_degraded` as the preferred honest-stack default semantic;
- whether any remaining docs in the parent scope still require stale `facade_only` or `CAPABILITY_DENIED`-only wording;
- whether the final parent verification should include both the full compose smoke and the local activation-ready E2E smoke.

Suggested parent verification commands:

```bash
python3 scripts/smoke_openclaw_activation_ready_e2e.py
docker compose config --quiet
docker compose up -d --build
docker compose --profile smoke run --rm smoke-stack
docker compose down --volumes --remove-orphans
```

The compose commands are intentionally parent-owned because this sidecar is support-only and does not modify runtime or compose behavior.

## 6. Sidecar Verification

Commands run by this sidecar:

```bash
python3 scripts/smoke_openclaw_activation_ready_e2e.py
docker compose config --quiet
```

Result:

- `python3 scripts/smoke_openclaw_activation_ready_e2e.py`: passed, `13/13` rows.
- `docker compose config --quiet`: passed.

Not run by this sidecar:

- `docker compose up -d --build`
- `docker compose --profile smoke run --rm smoke-stack`
- `docker compose down --volumes --remove-orphans`

Those full-stack commands are intentionally left for the parent owner because this support slice does not modify runtime, compose, or canonical truth.
