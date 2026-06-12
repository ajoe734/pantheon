# Management AI Dev Kernel Control Mode

Status date: 2026-06-11

## Purpose

Management AI can answer read-only questions in user mode, but SA/SD generation,
DevTaskPacket queueing, and repair handoff require a short-lived kernel control
mode session. The product default is intentionally fail-closed:

```env
PANTHEON_ASSISTANT_KERNEL_ENABLED=false
```

Use this runbook only on an internal dev VM. Do not use it for staging-live,
canary, live, or production.

## Enable Dev Control Mode

Preferred command from the Pantheon repo root:

```bash
scripts/enable_management_ai_dev_kernel.sh
```

The script defaults to the live dev compose project name:

```env
COMPOSE_PROJECT_NAME=pantheon
COMPOSE_FILE=docker-compose.yml
BFF_BASE_URL=http://127.0.0.1:18001
BFF_AUTH_TOKEN=pantheon-dev-browser:admin,operator:mfa:assistant.kernel.debug,assistant.kernel.repair
PANTHEON_STATUS_ROOT_HOST=/home/lupin/code/pantheon
PANTHEON_STATUS_ROOT_CONTAINER=/workspace/status-root
PANTHEON_ASSISTANT_KERNEL_ENABLED=true
PANTHEON_BFF_STUB_CAPABILITIES=assistant.kernel.debug,assistant.kernel.repair
```

When `PANTHEON_STATUS_ROOT_HOST` is not supplied, the script first tries to read
the supervisor status root from
`/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json`.
If that file is absent, it falls back to the repo root where the script is run.

Manual equivalent:

```bash
PANTHEON_STATUS_ROOT_HOST=/home/lupin/code/pantheon \
PANTHEON_ASSISTANT_KERNEL_ENABLED=true \
PANTHEON_BFF_STUB_CAPABILITIES=assistant.kernel.debug,assistant.kernel.repair \
docker compose -p pantheon up -d --no-deps --force-recreate operator-bff
```

The `-p pantheon` flag matters on the shared dev VM. Running compose without the
project name can create a second empty project and fail on the already-bound BFF
port.

The status-root host path also matters. It must be the same root used by the
supervisor config's `paths.status_file`. If BFF mounts a deploy checkout while
the supervisor drains from the main runtime checkout, Management AI can answer
provider questions but `queueTaskPacket` and assistant dev-bridge readback will
point at the wrong `.orchestrator/assistant-dev-packets` directory.

## Dev Deploy Persistence

`scripts/deploy_nonprod_vm.sh --environment dev` now exports the same dev-only
kernel overlay during root stack deployment:

- `PANTHEON_ASSISTANT_KERNEL_ENABLED=true`
- `PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH=/data/bff/assistant-control-mode.json`
- `PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS=300`
- `PANTHEON_BFF_STUB_CAPABILITIES=assistant.kernel.debug,assistant.kernel.repair`
- `PANTHEON_STATUS_ROOT_HOST=<dev remote repo root by default>`
- `PANTHEON_STATUS_ROOT_CONTAINER=/workspace/status-root`

Override `DEV_STATUS_ROOT_HOST` when the supervisor drains from a different
runtime root than the deploy checkout. The staging-live env file remains
explicitly kernel-disabled.

## Verify

All final-contract Management AI operator POST smokes require a stable
`Idempotency-Key` header. Missing keys return `400 VALIDATION_FAILED` with
`precondition_failed=idempotency_key`; this is an idempotency guardrail, not an
OpenClaw provider failure. Reusing the same key with the same payload should
replay the stored response, while reusing it with a changed payload should fail
with `409 IDEMPOTENCY_CONFLICT`.

Kernel flag and control-mode posture:

```bash
curl -fsS http://127.0.0.1:18001/bff/assistant/mode | jq .
```

Expected:

- `data.kernel_enabled=true`
- `data.control_mode.configured=true`
- `data.control_mode.active=false` until an operator activates it

Supervisor and dev bridge alignment:

```bash
curl -fsS http://127.0.0.1:18001/bff/assistant/orchestrator/status \
  | jq '{supervisor:.data.supervisor, assistantDevBridge:.data.assistantDevBridge}'
```

Expected:

- `data.supervisor.lifecycle=running`
- `data.assistantDevBridge.inbox.exists=true`
- the inbox readback points at the same root as the supervisor drain path

Wrong-passphrase probe:

```bash
curl -fsS -i \
  -H 'Authorization: Bearer pantheon-dev-browser:admin:mfa:assistant.kernel.debug,assistant.kernel.repair' \
  -H 'Content-Type: application/json' \
  --data '{"passphrase":"wrong phrase for precondition probe","mode":"kernel_repair","reason":"probe control-mode preconditions only"}' \
  http://127.0.0.1:18001/bff/assistant/control-mode/activate
```

Expected failure after the dev flag is enabled:

- HTTP `403`
- `invalid_passphrase`

If the failure says kernel sessions are disabled, the BFF was recreated without
`PANTHEON_ASSISTANT_KERNEL_ENABLED=true`.

Provider smoke:

```bash
curl -fsS \
  -H 'Authorization: Bearer pantheon-dev-browser:admin:mfa:assistant.kernel.debug,assistant.kernel.repair' \
  -H 'Idempotency-Key: mgmt-ai-dev-kernel-smoke' \
  -H 'Content-Type: application/json' \
  --data '{"question":"Report Management AI OpenClaw provider readiness only.","conversationId":"mgmt-ai-dev-kernel-smoke","useAssistantProvider":true}' \
  http://127.0.0.1:18001/bff/management/nl/ask | jq '.data.providerStatus'
```

Expected:

- `status=completed`
- `used=true`
- `fallback=null`

## Positive SA/SD Queue Smoke

The full SA/SD queue smoke requires the existing control-mode passphrase. Do
not rotate or print the passphrase for a smoke run.

```bash
PANTHEON_ASSISTANT_CONTROL_PASSPHRASE='<existing control-mode passphrase>' \
scripts/smoke_management_ai_control_mode_queue.sh
```

Optional overrides:

```env
BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io
BFF_AUTH_TOKEN=pantheon-dev-browser:admin:mfa:assistant.kernel.debug,assistant.kernel.repair
SESSION_ID=mgmt-ai-control-mode-smoke-manual
TASK_OWNER=assistant-supervisor
```

The smoke verifies:

- BFF health is reachable.
- `kernel_enabled=true` and control passphrase is configured.
- control mode activates as `kernel_repair`.
- `/bff/assistant/dev-docs/generate` returns HTTP `201`.
- generated SA/SD artifacts are archived.
- a signed DevTaskPacket is queued into the supervisor inbox.
- `/bff/assistant/orchestrator/status` reports supervisor/provider/dev-bridge
  readback after queueing.

If the script fails with `invalid_passphrase`, the runtime is healthy but the
operator did not provide the current passphrase. If it fails with
`not_active`, activation did not complete and `/dev-docs/generate` is correctly
failing closed.

## Positive OpenClaw Repair E2E Smoke

The queue smoke above proves SA/SD generation and supervisor packet queueing,
but it does not prove VM file writes through OpenClaw. The full repair smoke
requires the same existing control-mode passphrase and intentionally writes only
a sentinel file inside a clean repair task worktree.

```bash
PANTHEON_ASSISTANT_CONTROL_PASSPHRASE=<existing-control-mode-passphrase> scripts/smoke_management_ai_openclaw_repair_e2e.sh
```

Optional overrides:

```env
BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io
BFF_AUTH_TOKEN=pantheon-dev-browser:admin:mfa:assistant.kernel.debug,assistant.kernel.repair
SESSION_ID=mgmt-ai-openclaw-repair-smoke-manual
REPAIR_REPO_KEY=execute-plans
REPAIR_MERGE_TARGET=dev
REPAIR_SCOPE=tmp/management-ai-openclaw-smoke
TASK_OWNER=assistant-supervisor
```

The smoke verifies:

- all Management AI/assistant POST requests include stable `Idempotency-Key`
  headers;
- control mode activates as `kernel_repair`;
- `/bff/assistant/repair-worktrees/prepare` returns a clean task worktree;
- `/bff/management/nl/ask` forwards `openclaw.repair` metadata to the provider;
- provider status reports `used=true`, `completed`, and
  `workspaceClass=task_worktree`;
- OpenClaw writes the sentinel file inside the declared repair scope;
- `/bff/assistant/dev-docs/generate` returns HTTP `201` with
  `queueTaskPacket=true`;
- the supervisor drains the queued DevTaskPacket and reports a processed
  receipt through `/bff/assistant/orchestrator/status`.

The smoke does not commit, push, deploy, or touch broker/live/capital/runtime
state. A failure at the sentinel step means read/status may work, but
write-capable Management AI repair is not yet proven.

## Frontend Activation Preconditions

The passphrase is only one activation factor. Control mode also requires:

- `PANTHEON_ASSISTANT_KERNEL_ENABLED=true`
- role `admin` or `operator`
- MFA on the bearer identity
- a capability beginning with `assistant.kernel`
- a configured control-mode passphrase

For dev stub auth, `env/dev-management-ai-kernel.env.example` and the enable
script add `assistant.kernel.debug` and `assistant.kernel.repair` to stub
tokens. A browser operator still needs an admin/operator identity with MFA.

Never put the control-mode passphrase, broker credentials, API tokens, private
keys, or other secrets in Lovable frontend environment variables.
