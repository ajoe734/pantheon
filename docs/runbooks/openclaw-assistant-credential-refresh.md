# Runbook: OpenClaw Assistant Credential Refresh

This runbook describes how to verify and refresh the service-user OAuth credentials used by the OpenClaw gateway assistant providers (Codex and Claude).

## 1. Context

The OpenClaw gateway adapter invokes Codex and Claude CLI binaries inside a Docker container. These binaries rely on credentials stored in mounted home directories:
- **Codex**: `.codex` directory (default: `/srv/pantheon-assistant/.codex`)
- **Claude**: `.claude` directory (default: `/srv/pantheon-assistant/.claude`)

These directories are mounted from the host into the container. For the CLIs to maintain session state and refresh tokens automatically, these mounts **MUST** be in `rw` (read-write) mode. If they are in `ro` mode, the providers will eventually become `degraded` once the current session expires, even if the host files are valid.

## 2. Diagnostics

### 2.1 Check Readiness and Mount Mode via API
You can check the readiness and mount posture of all assistant providers via the adapter's API:

```bash
curl -s http://localhost:8104/api/openclaw-adapter/assistant/providers?auth_probe=true | jq .
```

- If `ready` is `false` and `degraded_reason` is `codex_auth_unavailable` or `claude_auth_failure`, the credentials have expired or are missing.
- If `mount_mode` is `ro`, it **WILL** prevent the CLI from saving refreshed tokens. The smoke script will issue a warning in this case.

### 2.2 Run Smoke Test
The smoke script performs a minimal non-interactive invocation to verify end-to-end connectivity:

```bash
./scripts/openclaw-assistant-provider-smoke.sh
```

## 3. Refreshing Credentials

If credentials have expired, they must be refreshed on the host machine where the volumes are stored.

### 3.1 Prerequisite: Host Access
You must have access to the host machine as a user with permissions to write to `/srv/pantheon-assistant/`.

### 3.2 Refresh Codex
1. Log in to the host machine.
2. Run the login command as the `pantheon-assistant` user (or using `sudo` with `CODEX_HOME` set):
   ```bash
   export CODEX_HOME=/srv/pantheon-assistant/.codex
   codex login
   ```
3. Follow the interactive OAuth flow in your browser.
4. Verify:
   ```bash
   codex exec "say hello"
   ```

### 3.3 Refresh Claude
1. Log in to the host machine.
2. Run the login command:
   ```bash
   export CLAUDE_CONFIG_DIR=/srv/pantheon-assistant/.claude
   claude login
   ```
3. Follow the interactive OAuth flow.
4. Verify:
   ```bash
   claude -p "say hello"
   ```

## 4. Troubleshooting

### 4.1 Permission Denied
If the adapter reports `wrong_owner` or `host_mount_unreadable`:
- Ensure the directories on the host are owned by the UID expected by the container (default: `pantheon-assistant` user).
- Permissions should be `0700`.

### 4.2 Mount Mode is `ro`
If `PANTHEON_ASSISTANT_CREDENTIAL_MOUNT_MODE` is set to `ro` in `docker-compose.yml`, the CLIs will not be able to update their token files.
**Recommendation**: Change to `rw` unless you are sure the token is long-lived and does not require rotation.

### 4.3 Degraded Mode
When credentials expire, the adapter status becomes `degraded`. The BFF will then apply deterministic fallbacks. Kernel-mode operations (debug/repair) will be disabled until credentials are refreshed.
