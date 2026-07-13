# Management AI Dev Kernel Control Mode

Status date: 2026-07-13

## Purpose

Management AI can answer read-only questions in user mode, but SA/SD generation,
DevTaskPacket queueing, and repair handoff require a short-lived kernel control
mode session. The product default is intentionally fail-closed:

```env
PANTHEON_ASSISTANT_KERNEL_ENABLED=false
PANTHEON_BFF_AUTH_STUB=false
PANTHEON_BFF_AUTH_MODE=strict
PANTHEON_BFF_STUB_CAPABILITIES=
```

Use this runbook only on an internal dev VM. Do not use it for staging-live,
canary, live, or production.

## Enable Dev Control Mode

Preferred command from the Pantheon repo root:

```bash
BFF_AUTH_TOKEN='<short-lived privileged JWT>' \
  scripts/enable_management_ai_dev_kernel.sh
```

`BFF_AUTH_TOKEN` is required and has no tracked fallback. The exact public
browser credential (`pantheon-dev-browser:viewer`) is capability-free and
read-only, so the script rejects it before changing the compose service. The
script also calls `/bff/me` before any container mutation and requires an
operator/admin identity with MFA plus `assistant.kernel.debug` or
`assistant.kernel.repair`. It then reads `/bff/assistant/mode` and refuses to
restart while that actor has an active or session-bound control mode. An
unreachable BFF or invalid/under-scoped token is a hard block.

Before recreation, the script captures the running container's auth and policy
environment in a mode-0600 temporary file. Unspecified issuer, audience,
tenant/allowed-tenant, role mapping, MFA policy, login profile, and signing
settings are preserved from that snapshot. A failed recreate or failed exact
postcondition automatically recreates `operator-bff` with the captured policy.
The success postcondition is exactly kernel enabled, passphrase configured, and
control mode inactive with no management session.

The script defaults to the live dev compose project name:

```env
COMPOSE_PROJECT_NAME=pantheon
COMPOSE_FILE=docker-compose.yml
BFF_BASE_URL=http://127.0.0.1:18001
BFF_AUTH_TOKEN=<required short-lived privileged JWT>
PANTHEON_STATUS_ROOT_HOST=/home/lupin/code/pantheon
PANTHEON_STATUS_ROOT_CONTAINER=/workspace/status-root
PANTHEON_ASSISTANT_KERNEL_ENABLED=true
PANTHEON_BFF_AUTH_STUB=false
PANTHEON_BFF_AUTH_MODE=strict
PANTHEON_BFF_STUB_CAPABILITIES=
```

When `PANTHEON_STATUS_ROOT_HOST` is not supplied, the script first tries to read
the supervisor status root from
`/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json`.
If that file is absent, it falls back to the repo root where the script is run.

Manual equivalent: policy inputs shown for diagnostic reference only.

```bash
# Obtain these existing values from the governed dev environment. Do not put
# their literal values in shell history, source, logs, or this runbook.
export PANTHEON_BFF_JWT_SECRET='<governed dev signing secret>'
export PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON='<governed profile JSON>'
export PANTHEON_BFF_JWT_ISSUER='pantheon-dev'
export PANTHEON_BFF_JWT_AUDIENCE='bff-operators'
export PANTHEON_BFF_TENANT_ID='tenant-dev'
export PANTHEON_BFF_ALLOWED_TENANTS='tenant-dev'

PANTHEON_STATUS_ROOT_HOST=/home/lupin/code/pantheon \
PANTHEON_ASSISTANT_KERNEL_ENABLED=true \
PANTHEON_BFF_AUTH_STUB=false \
PANTHEON_BFF_AUTH_MODE=strict \
PANTHEON_BFF_STUB_CAPABILITIES= \
docker compose -p pantheon up -d --no-deps --force-recreate operator-bff
```

Do not use the direct Compose command as the normal enable procedure: it has no
preflight, inactive-session guard, exact postcondition, or rollback. Use
`scripts/enable_management_ai_dev_kernel.sh`. If an emergency manual operation
is unavoidable, first capture the existing container environment, preserve all
listed auth/policy keys, and restore that snapshot if either recreation or
authenticated mode readback fails.

The `-p pantheon` flag matters on the shared dev VM. Running compose without the
project name can create a second empty project and fail on the already-bound BFF
port.

The status-root host path also matters. It must be the same root used by the
supervisor config's `paths.status_file`. If BFF mounts a deploy checkout while
the supervisor drains from the main runtime checkout, Management AI can answer
provider questions but `queueTaskPacket` and assistant dev-bridge readback will
point at the wrong `.orchestrator/assistant-dev-packets` directory.
For dev kernel mode, the BFF status-root mount must be read/write. SA/SD
archive and `queueTaskPacket` writes are intentionally mediated by BFF control
mode and must land in the same host tree that the supervisor drains.

## Dev Deploy Persistence

`scripts/deploy_nonprod_vm.sh --environment dev` now exports the same dev-only
kernel overlay during root stack deployment:

- `PANTHEON_ASSISTANT_KERNEL_ENABLED=true`
- `PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH=/data/bff/assistant-control-mode.json`
- `PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS=300`
- `PANTHEON_BFF_AUTH_STUB=false`
- `PANTHEON_BFF_AUTH_MODE=strict`
- `PANTHEON_BFF_STUB_CAPABILITIES=`
- `PANTHEON_STATUS_ROOT_HOST=<dev remote repo root by default>`
- `PANTHEON_STATUS_ROOT_CONTAINER=/workspace/status-root`

Override `DEV_STATUS_ROOT_HOST` when the supervisor drains from a different
runtime root than the deploy checkout. The deploy script rejects attempts to
enable auth stub, permissive mode, or stub capabilities before SSH. The
staging-live env file remains explicitly kernel-disabled.

The BFF and GitHub environment still need independently provisioned dev-login
client profiles and a JWT signing secret. Those secrets are not created or
rotated by this repository. `PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON` is a
server-side map: each client fixes a unique subject, roles, tenant and allowed
tenants, capabilities, and MFA state. The login request cannot request or
override those claims. Duplicate subjects and invalid/cross-tenant profiles
fail closed. Kernel operator profiles may carry `assistant.kernel.*`; the Agora
deploy CI profile must be the distinct subject `pantheon-dev-ci-agora`, role
`operator` only, tenant `tenant-dev` only, no kernel capability, and no MFA
assertion. Do not restore structured-token or stub-capability fallbacks.

The external GitHub environment must provide `DEV_BFF_JWT_SECRET`,
`DEV_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON`, `DEV_BFF_CI_CLIENT_ID`, and
`DEV_BFF_CI_CLIENT_SECRET`. The deploy script rejects partial, whitespace-only,
or malformed profile configuration and streams secrets through SSH stdin; they
are absent from gcloud argv and its environment. A local dry-run may omit them
to inspect the safe configuration, but the governed GitHub workflow blocks
every dev root/BFF deployment until all four exist.

The non-prod workflow treats missing GitHub dev-login credentials as a hard
`BLOCKED` outcome before cloud authentication or deployment. When credentials
exist, it exchanges the CI credential through `/bff/auth/dev-login`, validates
the response as a bounded compact bearer JWT, verifies `/bff/me` is the exact
least-role/single-tenant CI identity, and uses only that short-lived JWT for the
restart-persistence smoke.

Repository support is not the live completion claim. Until the governed profile
JSON, distinct CI credential, JWT signing secret, and a separate MFA-backed
kernel-operator credential are provisioned and the authenticated hosted smokes
pass, dev auth/kernel qualification remains `BLOCKED`, not done.

## Verify

All final-contract Management AI operator POST smokes require a stable
`Idempotency-Key` header. Missing keys return `400 VALIDATION_FAILED` with
`precondition_failed=idempotency_key`; this is an idempotency guardrail, not an
OpenClaw provider failure. Reusing the same key with the same payload should
replay the stored response, while reusing it with a changed payload should fail
with `409 IDEMPOTENCY_CONFLICT`.

Kernel flag and control-mode posture:

```bash
curl -fsS \
  -H "Authorization: Bearer ${BFF_AUTH_TOKEN}" \
  http://127.0.0.1:18001/bff/assistant/mode | jq .
```

Expected:

- `data.kernel_enabled=true`
- `data.control_mode.configured=true`
- `data.control_mode.active=false` until an operator activates it

Supervisor and dev bridge alignment:

```bash
curl -fsS \
  -H "Authorization: Bearer ${BFF_AUTH_TOKEN}" \
  http://127.0.0.1:18001/bff/assistant/orchestrator/status \
  | jq '{supervisor:.data.supervisor, assistantDevBridge:.data.assistantDevBridge}'
```

Expected:

- `data.supervisor.lifecycle=running`
- `data.assistantDevBridge.inbox.exists=true`
- the inbox readback points at the same root as the supervisor drain path

Wrong-passphrase probe:

```bash
curl -fsS -i \
  -H "Authorization: Bearer ${BFF_AUTH_TOKEN}" \
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
  -H "Authorization: Bearer ${BFF_AUTH_TOKEN}" \
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
BFF_AUTH_TOKEN='<short-lived privileged JWT>' \
scripts/smoke_management_ai_control_mode_queue.sh
```

Optional overrides:

```env
BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io
BFF_AUTH_TOKEN=<required short-lived privileged JWT>
SESSION_ID=mgmt-ai-control-mode-smoke-manual
TASK_OWNER=Codex
TASK_REVIEWER=Claude
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
- the EXIT cleanup requires HTTP `202` from deactivation and authoritative
  inactive mode readback after success or any post-activation failure. An
  original test error remains the reported failure; cleanup failure converts an
  otherwise successful run to failure.

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
PANTHEON_ASSISTANT_CONTROL_PASSPHRASE=<existing-control-mode-passphrase> \
BFF_AUTH_TOKEN='<short-lived privileged JWT>' \
scripts/smoke_management_ai_openclaw_repair_e2e.sh
```

Optional overrides:

```env
BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io
BFF_AUTH_TOKEN=<required short-lived privileged JWT>
SESSION_ID=mgmt-ai-openclaw-repair-smoke-manual
REPAIR_REPO_KEY=execute-plans
REPAIR_MERGE_TARGET=dev
REPAIR_SCOPE=tmp/management-ai-openclaw-smoke
TASK_OWNER=Codex
TASK_REVIEWER=Claude
POLL_SECONDS=360
```

The smoke verifies:

- all Management AI/assistant POST requests include stable `Idempotency-Key`
  headers;
- control mode activates as `kernel_repair`;
- `/bff/assistant/repair-worktrees/prepare` returns a clean task worktree;
- `/bff/management/nl/ask` forwards `openclaw.repair` metadata to the provider;
  the request must include the same `sessionId` used for control-mode
  activation so the provider receives the repair workspace instead of a
  read-only session mismatch;
- provider status reports `used=true`, `completed`, and
  `workspaceClass=task_worktree`;
- OpenClaw writes the sentinel file inside the declared repair scope;
- `/bff/assistant/dev-docs/generate` returns HTTP `201` with
  `queueTaskPacket=true`;
- generated DevTaskPackets use repository-recognized worker owners/reviewers
  such as `Codex` and `Claude`, not `assistant-supervisor` or `Supervisor`;
- the supervisor drains the queued DevTaskPacket and reports a processed
  receipt through `/bff/assistant/orchestrator/status`.
- EXIT cleanup requires HTTP `202` deactivation and authoritative inactive
  readback, including after a provider, sentinel, or queue failure.

The dev supervisor default poll interval is 300 seconds, so E2E smoke polling
must cover at least one full supervisor tick after `queueTaskPacket=true`.

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

Dev kernel mode never enables stub auth or global stub capabilities. A governed
server-side kernel-operator profile must issue a signed short-lived JWT with a
unique subject, admin/operator role, MFA assertion, a single allowed tenant,
and the required kernel capability. The public browser viewer and the
least-role CI profile cannot activate control mode.

The enable and smoke scripts write Authorization headers to mode-0600 temporary
files and remove the exported JWT from unrelated child-process environments.

Never put the control-mode passphrase, broker credentials, API tokens, private
keys, or other secrets in Lovable frontend environment variables.
