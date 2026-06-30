# Runbook: OpenClaw Assistant Credential Refresh

This runbook covers the dedicated service-user OAuth credentials used by the
OpenClaw gateway assistant providers. It intentionally avoids API-key recovery
paths; Codex and Claude authenticate through their account-login CLI state.

## Context

The `openclaw-gateway-adapter` invokes the Codex and Claude CLIs inside the
adapter container. Their account sessions live in mounted service-user
directories.

| Provider | Host env | Container env | Default host path | Default container path | Required mode for refresh |
|---|---|---|---|---|---|
| Codex | `PANTHEON_ASSISTANT_CODEX_HOST_HOME` | `PANTHEON_ASSISTANT_CODEX_CONTAINER_HOME` / `CODEX_HOME` | `/srv/pantheon-assistant/.codex` | `/home/pantheon-assistant/.codex` | `rw` |
| Claude | `PANTHEON_ASSISTANT_CLAUDE_HOST_CONFIG_DIR` | `PANTHEON_ASSISTANT_CLAUDE_CONTAINER_CONFIG_DIR` / `CLAUDE_CONFIG_DIR` | `/srv/pantheon-assistant/.claude` | `/home/pantheon-assistant/.claude` | `rw` |

`PANTHEON_ASSISTANT_CREDENTIAL_MOUNT_MODE=rw` is required for both providers if
the container is expected to save refreshed tokens. `ro` can be used only for a
short-lived read-only inspection window; once the current session expires, the
provider must degrade until credentials are refreshed through a writable path.

## Diagnostics

First confirm that the adapter itself is alive. This endpoint is a no-op
liveness check and does not require assistant credentials:

```bash
curl -fsS http://localhost:8104/livez | jq .
```

Then probe provider readiness with an auth check:

```bash
curl -fsS 'http://localhost:8104/api/openclaw-adapter/assistant/providers?auth_probe=true' | jq .
```

Expected readiness fields:

| Field | Meaning |
|---|---|
| `ready` | Provider can currently run with the mounted session. |
| `auth_status` | `ready`, `not_checked`, `failed`, or `mount_unavailable`. |
| `mount_mode` | Sanitized mount posture, `rw`, `ro`, or `invalid`. |
| `credential_mount` | Sanitized host-source/container-target metadata; raw mount paths and token contents are not exposed. |
| `degraded_reason` | Stable reason to route fallback or repair action. |

Run the full smoke after the adapter is reachable:

```bash
./scripts/openclaw-assistant-provider-smoke.sh
```

The smoke performs `/livez`, provider readiness with `auth_probe=true`, and tiny
non-interactive invocations. It prints readiness metadata and provider status
only. Do not paste `.codex`, `.claude`, token files, browser codes, or raw CLI
session contents into logs.

## Refresh Paths

### Preferred: Host Refresh

Use this when you can access the host path that backs the container mount.

1. Confirm the service-user directories exist, are owned by the dedicated
   service user, and are not group/world-readable:

   ```bash
   sudo id -u pantheon-assistant >/dev/null 2>&1 || \
     sudo useradd --uid 10001 --home-dir /srv/pantheon-assistant --no-create-home --shell /usr/sbin/nologin pantheon-assistant
   sudo install -d -m 0750 -o pantheon-assistant -g pantheon-assistant /srv/pantheon-assistant
   sudo install -d -m 0700 -o pantheon-assistant -g pantheon-assistant /srv/pantheon-assistant/.codex
   sudo install -d -m 0700 -o pantheon-assistant -g pantheon-assistant /srv/pantheon-assistant/.claude
   ```

   The adapter image contains the same `pantheon-assistant` UID (`10001`) so
   container-side owner checks match the host-owned credential mounts.

2. Refresh Codex on the host:

   ```bash
   sudo -u pantheon-assistant env CODEX_HOME=/srv/pantheon-assistant/.codex codex login
   sudo -u pantheon-assistant env CODEX_HOME=/srv/pantheon-assistant/.codex codex exec "Reply with exactly: smoke-ok"
   ```

3. Refresh Claude on the host:

   ```bash
   sudo -u pantheon-assistant env CLAUDE_CONFIG_DIR=/srv/pantheon-assistant/.claude claude auth login
   sudo -u pantheon-assistant env CLAUDE_CONFIG_DIR=/srv/pantheon-assistant/.claude claude -p "Reply with exactly: smoke-ok"
   ```

4. Re-run the provider readiness probe and smoke script.

### Container Refresh

Use this only when `mount_mode` is `rw`. If the mount is `ro`, container login
may appear to complete but the refreshed token cannot be saved back to the
mounted credential directory.

1. Confirm writable mode:

   ```bash
   curl -fsS 'http://localhost:8104/api/openclaw-adapter/assistant/providers?auth_probe=true' | jq '.data[] | {provider, mount_mode, auth_status, degraded_reason}'
   ```

2. Refresh Codex inside the adapter container:

   ```bash
   docker compose exec openclaw-gateway-adapter sh -lc 'test "${PANTHEON_ASSISTANT_CREDENTIAL_MOUNT_MODE:-rw}" = rw && CODEX_HOME="${PANTHEON_ASSISTANT_CODEX_CONTAINER_HOME:-/home/pantheon-assistant/.codex}" codex login'
   ```

3. Refresh Claude inside the adapter container:

   ```bash
   docker compose exec openclaw-gateway-adapter sh -lc 'test "${PANTHEON_ASSISTANT_CREDENTIAL_MOUNT_MODE:-rw}" = rw && CLAUDE_CONFIG_DIR="${PANTHEON_ASSISTANT_CLAUDE_CONTAINER_CONFIG_DIR:-/home/pantheon-assistant/.claude}" claude auth login'
   ```

4. Re-run `./scripts/openclaw-assistant-provider-smoke.sh`.

## Degraded Behavior

| Provider | Readiness degraded reason | Invocation result | Operator action |
|---|---|---|---|
| Codex | `codex_auth_unavailable` | HTTP `503` provider error `CODEX_AUTH_UNAVAILABLE` | Refresh Codex credentials, then rerun smoke. |
| Codex | `codex_mount_<status>` | Provider unavailable before invocation | Fix mount policy, ownership, permissions, or mode. |
| Claude | `claude_auth_failure` | HTTP `200` with `status=degraded`, `degraded_reason=auth_failure` | Refresh Claude credentials, then rerun smoke. |
| Claude | `claude_mount_<status>` | HTTP `200` with `status=degraded` | Fix mount policy, ownership, permissions, or mode. |
| Any | `*_binary_not_found` or `*_version_probe_failed` | Provider unavailable | Fix container image/path before refreshing credentials. |

When auth is missing or expired, assistant provider status is degraded. User-mode
flows must use deterministic fallback. Kernel debug/repair flows must remain
disabled until readiness returns `ready=true` with `auth_status=ready`.

## Mount Mode Decision

- Use `rw` for normal operation for both `.codex` and `.claude`.
- Use `ro` only for temporary inspection where token refresh is intentionally
  disabled and degraded status after expiry is acceptable.
- If a provider is `ready=true` while `mount_mode=ro`, treat that as a warning:
  the current session works, but automatic refresh cannot be persisted.
- If a provider is degraded and `mount_mode=ro`, switch to `rw` before attempting
  container refresh, or refresh on the host path directly.

## Verification Checklist

1. `/livez` returns HTTP 200.
2. `/assistant/providers?auth_probe=true` reports provider `ready=true` and
   `auth_status=ready` for the provider being restored.
3. `mount_mode` is `rw` for any provider expected to refresh automatically.
4. `./scripts/openclaw-assistant-provider-smoke.sh` completes.
5. Logs and copied output contain no token file contents or personal home paths.
