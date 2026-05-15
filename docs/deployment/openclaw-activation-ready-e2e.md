# OpenClaw Activation-Ready E2E Profile

Status: task evidence for `SVC-OPENCLAW-ACTIVATION-READY-E2E`

This profile proves the OpenClaw adapter is activation-ready without enabling
production broker execution, real capital binding, or live order paths.

## Scope

The smoke starts local fake fixtures for:

- OpenClaw upstream gateway: `/healthz`, `/readyz`, capabilities, sessions,
  tools, workflows, and jobs.
- runtime-manager: active paper RuntimeBinding lookup.
- broker sidecar: paper simulation order submit/list/read.

The adapter path under test is still the real `openclaw-gateway-adapter`
boundary code. Upstream, runtime-manager, and broker calls go through the
adapter's normal HTTP clients.

## Run

Local:

```bash
python3 scripts/smoke_openclaw_activation_ready_e2e.py
```

Compose:

```bash
docker compose --profile openclaw-activation-ready-e2e run --rm openclaw-activation-ready-e2e
```

The compose profile publishes no ports and is not part of the default stack.
It builds the task-specific adapter smoke image from
`services/openclaw-gateway-adapter/Dockerfile` so the container includes the
FastAPI/httpx dependencies required by the real adapter path.

## Gates

Default posture remains fail-closed:

- `OPENCLAW_PRODUCTION_BROKER_ENABLED=false`
- `OPENCLAW_CAPITAL_BINDING_ENABLED=false`
- `OPENCLAW_LIVE_ADAPTER_ENABLED=false`
- `PANTHEON_LIVE_BROKER_ENABLED=false`

The smoke opens only the task-scoped paper simulation gate inside the test
process:

- `OPENCLAW_PAPER_ADAPTER_ENABLED=true`
- `OPENCLAW_BROKER_SIDECAR_URL=<fake broker>`
- `OPENCLAW_RUNTIME_MANAGER_URL=<fake runtime-manager>`
- `OPENCLAW_ALLOWED_TOOLS=research.search`
- `OPENCLAW_ALLOWED_WORKFLOWS=research.daily_scan`

Live execution is still denied by `POST /api/openclaw-adapter/broker/live/orders`
with `LIVE_EXECUTION_DISABLED`.

## Acceptance Evidence

The smoke verifies:

- default `/livez` is OK while `/readyz` degrades when upstream is absent;
- default capabilities report `activation_state=upstream_client_degraded`
  only with an upstream degraded envelope, while broker/live remain deferred;
- default session create remains fail-closed when upstream is absent and may
  return a typed upstream-unavailable envelope instead of a legacy facade-only
  capability-denied stub;
- fake upstream capabilities are reachable in the activation-ready path;
- fake upstream capabilities report `activation_state=upstream_client_ready`;
- lifecycle session create persists an active Pantheon-owned session;
- effective tools are policy allowlist intersected with upstream tools;
- disallowed broker/paper tool invocation is denied;
- paper order submission succeeds only with an active fake paper RuntimeBinding;
- paper adapter audit records the simulated handoff;
- live order submission is explicitly denied.
