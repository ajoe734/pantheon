# Auto Worker Readiness

Status: current local runtime truth
Recorded: 2026-05-03
Task: ORCH-AUTOWORKER-READINESS-RECOVERY

## Scope

This note explains which local auto workers can safely receive supervisor
dispatches and which ones are installed but blocked by auth/profile readiness.
It is based on:

```bash
python3 .orchestrator/doctor.py --json --no-write
```

The provider list can change when credentials are refreshed. Re-run the doctor
before enabling a worker in `ready_dispatcher` fallback lists.

## Current Matrix

| Worker | Auto dispatch | Current state | Blocking or caution |
| --- | --- | --- | --- |
| Claude | Yes, after rate window recovers | Authenticated Claude CLI profile at `/home/lupin/.claude` | Recent run hit a Claude five-hour rate event with reset at `2026-05-03T16:00:00Z`; avoid relying on it for immediate reviewer work before reset. |
| Claude2 | No | Claude CLI is installed, separate profile configured | Missing unauthenticated profile credentials at `/home/lupin/.claude2/.claude/.credentials.json`. |
| Codex | Yes | Codex CLI dispatch works with per-run orchestrator approval flags | Primary safe local execution worker. |
| Codex2 | Conditional, not extra capacity | Configured as a Codex agent alias using the Codex adapter | Treat as a scheduling/profile label, not a guaranteed independent capacity pool; the human doctor provider list reports the shared `codex` provider, not a standalone `codex2` provider. |
| Gemini | No | Gemini CLI/extension installed | Non-interactive Gemini auth is not ready for `/home/lupin/.gemini/settings.json` / OAuth credential path. |
| Gemini2 | No | Gemini CLI/extension installed with configured model/env | Non-interactive auth is not ready for `/home/lupin/.gemini2/.gemini/settings.json`; Vertex/project env alone is insufficient. |
| Copilot | Auth-ready, but currently disabled for mainline dispatch | Copilot CLI and GitHub CLI path are present; doctor reports `auth_ready=true` | `ready_dispatcher.disabled_agents` currently includes `Copilot`; supported model list is currently `claude`; remove the disable only after deciding it should receive mainline tasks again. |
| Grok | Conditional through Copilot | Treated as Copilot model preference, not a standalone provider | No standalone Grok worker; only enable when Copilot model routing is verified. |

## Dispatch Rules

Mainline execution should only use workers where the latest doctor report shows:

```text
local_cli_worker_supported=true
supports_auto_approve=true
config_valid=true or config_valid is absent for that adapter
auth_ready=true or auth_ready is not required by that adapter
```

When a provider is installed but not auto-ready, keep it out of mainline owner
and reviewer fallbacks. It may remain documented as a future lane, but the
supervisor should not assign production tasks to it.

The supervisor also performs a provider config preflight before spawning local
CLI workers. For Codex adapters, it reads the provider's configured
`codex_home/config.toml` and fails closed when the installed CLI would reject
the profile. A bad profile is a provider readiness failure, not a persona or
task-assignment failure.

## Recovery Checklist

Claude2:

1. Log in with the isolated Claude2 HOME/profile.
2. Confirm `/home/lupin/.claude2/.claude/.credentials.json` or the configured
   OAuth token exists.
3. Run `python3 .orchestrator/doctor.py --json --no-write` and verify
   `claude2.local_cli_worker_supported=true` and `claude2.auth_ready=true`.
4. Add Claude2 back to fallback lists only after the doctor is green.

Gemini:

1. Create or refresh `/home/lupin/.gemini/settings.json` with the selected
   non-interactive auth type.
2. Ensure the required OAuth or environment-variable auth path exists.
3. Run the doctor and verify `gemini.auth_ready=true`.
4. Keep inbox fallback disabled unless a human explicitly wants manual handoff.

Gemini2:

1. Refresh `/home/lupin/.gemini2/.gemini/settings.json`.
2. Verify credentials, not just `GOOGLE_CLOUD_PROJECT` and model selection.
3. Confirm the doctor reports both local CLI support and auth readiness.

Copilot:

1. Run `gh auth status` through the configured `.orchestrator/bin/gh`.
2. Run `python3 .orchestrator/doctor.py --json --no-write`.
3. Confirm `copilot.auth_ready=true`.
4. Remove `Copilot` from `ready_dispatcher.disabled_agents` only when it should
   receive mainline work again.
5. Verify the desired model route; the current verified supported model is
   `claude`.

Codex:

1. Confirm the provider's configured `codex_home` exists. The main Codex lane
   uses `/home/lupin/.codex`; Codex2 lanes use `/home/lupin/.codex2`.
2. If `config.toml` sets `service_tier`, it must be one of `fast` or `flex`
   for the currently installed Codex CLI. `priority` is rejected by
   `codex-cli 0.130.0` and will stop auto workers before task execution.
3. Run `python3 .orchestrator/doctor.py --json --no-write` and verify each
   Codex provider reports `config_valid=true`.
4. If a worker log contains `Error loading config.toml`, repair the profile
   first; do not reassign the task or edit persona settings.

Codex2:

1. Confirm the isolated `CODEX_HOME` is intentional.
2. Use it as a scheduling label only if the underlying Codex capacity can
   actually run a second worker.

## Dashboard Expectations

The dashboard should communicate provider state as readiness, not as "broken":

- installed but unauthenticated means blocked, not missing;
- rate limited means temporarily deferred;
- sidecar-only or disabled lanes should not receive mainline tasks;
- execution-only local guard should not be confused with provider failure.

When the dashboard shows no running workers for a blocked provider, that is the
correct fail-closed behavior.

## Verification Commands

```bash
python3 .orchestrator/doctor.py --json --no-write
python3 .orchestrator/doctor.py
python3 scripts/supervisor_runtime_health.py --require-watchdog --json
rg -n '^service_tier' /home/lupin/.codex/config.toml /home/lupin/.codex2/config.toml 2>/dev/null || true
rg -n '"disabled_agents"|"sidecar_only_agents"' .orchestrator/config.json .orchestrator/config.local.json
tmux ls | rg 'pantheon-(dashboard|dashboard-tunnel|supervisor)' || true
```

`tmux` is only an operator convenience check. Supervisor/auto-worker readiness
must be judged from the watchdog-backed runtime health check above; dashboard
or tunnel sessions can stay alive after the supervisor process has died.
