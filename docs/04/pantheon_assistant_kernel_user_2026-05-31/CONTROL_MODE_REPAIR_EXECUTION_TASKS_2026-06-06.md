# Assistant Control Mode And Runtime Repair Execution Tasks

Date: 2026-06-06
Sprint: `2026-06-06-assistant-control-mode-runtime-repair`
Source context:

- `docs/04/pantheon_assistant_kernel_user_2026-05-31/USER_MODE_CONTRACTION.md`
- `docs/04/pantheon_assistant_kernel_user_2026-05-31/ASST_KERNEL_007_REPAIR_WORKFLOW.md`
- `services/control-plane/bff/assistant/control_mode.py`
- `services/control-plane/bff/assistant/mode_policy.py`
- `services/control-plane/bff/main.py`
- `docker-compose.yml`

## Problem

Management AI control mode exists, but the observed operator experience still
looks like user mode:

- `controlMode.active=false` for reviewer-context requests.
- Kernel activation is disabled unless `PANTHEON_ASSISTANT_KERNEL_ENABLED=true`.
- The dev browser token path is documented as `pantheon-dev-browser:reviewer`,
  which cannot activate control mode.
- Stub auth does not currently provide explicit `assistant.kernel*` capability
  claims to control-mode gates.
- The passphrase path is implemented, but the passphrase is only one activation
  factor; it cannot override RBAC, MFA, kernel feature flags, or capability
  checks.
- Management AI can explain degraded persona runtime state, but it still lacks a
  complete governed runtime repair action path for stale paper runtime,
  monitoring sessions, and telemetry bridge/ingest recovery.

This packet turns that gap into supervisor-visible execution tasks. It does not
authorize a browser-visible shell and does not make a secret phrase a privilege
bypass.

## Guardrails

- User mode remains the product default.
- Control mode requires kernel feature flag, MFA, explicit activation
  authority, passphrase, TTL, idle timeout, audit, and redaction.
- Reviewer role alone must not activate control mode.
- Any broader Management AI VM/repo access must go through the existing
  OpenClaw/Codex provider boundary, command policy, and repair worktree guard.
- Runtime repair must use BFF action catalog, command executor, runtime-manager
  protected API, or admin CLI paths. It must not add ad hoc shell writes from the
  Web API.
- Passphrases, bearer tokens, provider sessions, `.env` contents, and mounted
  credential paths must never appear in Management AI conversation readback,
  provider payloads, audit events, or task packets.
- Repo changes produced by kernel repair must use task branch/worktree,
  validation, commit, push, PR, checks, and merge workflow.

## Task Wave

| Task ID | Owner | Reviewer | Phase | Purpose |
|---|---|---|---|---|
| `ASST-CTRL-001` | Codex | Claude | Control-mode deployability | Make assistant kernel/control-mode deployment flags and passphrase store paths explicit in compose/deploy contracts. |
| `ASST-CTRL-002` | Codex2 | Claude | Activation authority | Add explicit activation capability plumbing for dev/staging auth and preserve reviewer denial without a granted activation capability. |
| `ASST-CTRL-003` | Claude | Codex2 | Management AI control UX | Improve Management AI control-mode status/error reporting and frontend-visible capability hints without leaking passphrases. |
| `ASST-RUNTIME-001` | Gemini | Codex | Runtime repair action catalog | Add governed runtime recovery action specs for stale paper runtime, monitoring sessions, telemetry bridge, and telemetry ingest probes. |
| `ASST-RUNTIME-002` | Codex | Claude | Runtime repair execution | Wire the approved runtime recovery actions to command executor/runtime-manager/admin CLI paths with audit receipts. |
| `ASST-SEC-002` | Claude2 | Codex | Security regression | Add focused regression tests for activation gates, passphrase redaction, command-policy denial, and runtime repair audit constraints. |

## Detailed Acceptance

### ASST-CTRL-001 Control-Mode Deployability

Artifacts:

- `docker-compose.yml`
- `docker-compose.staging-full.yml`
- `scripts/deploy_nonprod_vm.sh`
- `docs/04/pantheon_assistant_kernel_user_2026-05-31/USER_MODE_CONTRACTION.md`
- `services/control-plane/bff/tests/test_assistant_dev_compose_flags.py`

Acceptance:

- `operator-bff` exposes `PANTHEON_ASSISTANT_KERNEL_ENABLED` through compose
  with a conservative default of `false`.
- `operator-bff` exposes `PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH` pointing
  at durable BFF data storage, not `/tmp`, for dev/staging compose contracts.
- `PANTHEON_ASSISTANT_CONTROL_IDLE_TTL_SECONDS` is configurable and documented.
- The nonprod deploy script can set the same env values without hard-coding a
  plaintext passphrase.
- Existing assistant provider enablement remains independent from kernel
  enablement; provider enabled does not imply kernel enabled.
- Tests assert the compose env contract and the kernel-disabled default.

### ASST-CTRL-002 Activation Authority

Artifacts:

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/assistant/control_mode.py`
- `services/control-plane/bff/assistant/routes.py`
- `services/control-plane/bff/tests/test_assistant_sessions.py`
- `services/control-plane/bff/tests/test_assistant_security.py`
- `services/control-plane/bff/tests/test_management_nl_assistant_provider.py`

Acceptance:

- Introduce an explicit activation capability such as
  `assistant.kernel.activate`; debug/repair capabilities remain separate.
- Stub/dev auth can carry capability claims, for example:
  `Bearer pantheon-dev-operator:operator:mfa:assistant.kernel.activate,assistant.kernel.debug`.
- Production/JWT auth still prefers real capability claims from the identity
  provider.
- `reviewer` without `assistant.kernel.activate` remains rejected even with the
  correct passphrase and MFA.
- An explicitly authorized operator/admin with MFA, `assistant.kernel.activate`,
  a matching passphrase, and `PANTHEON_ASSISTANT_KERNEL_ENABLED=true` can
  activate `kernel_debug`.
- Error details distinguish missing role, missing MFA, missing activation
  capability, disabled kernel flag, missing passphrase configuration, and bad
  passphrase.
- Tests cover reviewer denial, operator activation, capability parsing, missing
  kernel flag, bad passphrase, TTL/idle expiry, and Management NL direct
  passphrase interception.

### ASST-CTRL-003 Management AI Control UX

Artifacts:

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/assistant/routes.py`
- `apps/management/src/**`
- `services/control-plane/bff/tests/test_management_nl_assistant_provider.py`

Acceptance:

- `/bff/assistant/mode` and `/bff/assistant/control-mode` expose enough
  machine-readable status for the UI to explain why control mode is inactive.
- Management NL answers for `/control status`, `/control off`, explicit
  passphrase commands, and direct passphrase matches return redacted questions
  and clear `controlCommand` values.
- UI shows user/control/kernel posture without implying that passphrase alone is
  enough.
- Failed explicit passphrase attempts are redacted before provider invocation,
  conversation persistence, and audit event persistence.
- Management AI context packs switch to `mode=kernel_debug` only while a valid
  activation is active for the same management session.

### ASST-RUNTIME-001 Runtime Repair Action Catalog

Artifacts:

- `services/control-plane/bff/action_catalog.py`
- `services/control-plane/bff/models.py`
- `services/control-plane/bff/BFF_API_CONTRACT.md`
- `OPERATOR_ACCEPTANCE_MATRIX.md`
- `docs/deployment/runtime-repair-control-mode-2026-06-06.md`

Acceptance:

- Define governed actions for paper runtime and telemetry recovery:
  `RestartPaperRuntime`, `RestartTelemetryBridge`,
  `TerminateStalePaperMonitoringSession`, `StartPaperMonitoringSession`, and
  `ProbeTelemetryIngest`.
- Each action declares required role/capability, confirmation requirement,
  idempotency behavior, audit receipt shape, and fallback path when BFF is down.
- Each action explicitly states whether it is read-only, restart-only, or
  session-mutating.
- No action grants live broker/capital authority.
- The contract explains that `totalTrades` may stay zero after recovery; the
  first success condition is fresh heartbeat and `staleness.age_seconds < 90`.

### ASST-RUNTIME-002 Runtime Repair Execution

Artifacts:

- `services/control-plane/bff/command_executor.py`
- `services/control-plane/bff/test_command_executor.py`
- `services/control-plane/bff/test_bff_write_gap_2026_05_28.py`
- `scripts/pantheon-admin` or existing admin CLI/runtime-manager integration
- `services/runtime-manager/**`

Acceptance:

- Approved runtime repair actions dispatch through runtime-manager protected
  API or admin CLI, not raw shell commands from BFF.
- Each command writes an audit receipt with actor, action id, target runtime or
  monitoring session, idempotency key, requested stage, and trace id.
- Stale `paper_runtime_monitoring` sessions can be terminated only when the
  heartbeat/session state proves staleness.
- Restart/probe flows update or verify telemetry projection freshness.
- Tests cover success, idempotent replay, stale-session guard, unauthorized
  caller, missing confirmation, and degraded dependency fallback.
- A smoke check proves persona health can recover from stale heartbeat to
  `last_heartbeat_at` near current time and `staleness.age_seconds < 90`.

### ASST-SEC-002 Security Regression

Artifacts:

- `services/control-plane/bff/tests/test_assistant_security.py`
- `services/control-plane/bff/assistant/tests/test_user_mode_regression.py`
- `services/openclaw-gateway-adapter/tests/**`
- `services/control-plane/bff/tests/test_management_nl_assistant_provider.py`

Acceptance:

- User mode cannot access shell, repo write, raw logs, docker, secret store,
  provider sessions, or command broker.
- Control mode activation fails closed when kernel env is false, passphrase is
  missing, passphrase is wrong, MFA is absent, activation capability is absent,
  or TTL/idle timeout is invalid.
- Explicit and direct passphrase attempts never persist raw passphrase text.
- Command broker denies `.env`, token/cookie/key paths, docker socket, root
  shell, destructive git commands, and direct production DB writes.
- Runtime repair commands require high-risk confirmation and write audit
  receipts.
- Prompt injection in logs or UI context cannot expand the tool allowlist.

## Dispatch

This packet can be materialized after operator acceptance by running:

```bash
python3 scripts/dispatch_assistant_control_mode_repair_2026-06-06.py
```

The dispatcher updates sprint metadata and creates the `ASST-CTRL-*`,
`ASST-RUNTIME-*`, and `ASST-SEC-002` task rows through
`scripts/ai_status.py assign`. It intentionally does not run automatically from
this document because `ai-status.json`, `current-work.md`, and
`.orchestrator/task-briefs/` are live supervisor state.

## Closeout Evidence Required

When the wave is complete, the owner must report:

- PR numbers and merge SHAs for each implementation task.
- Local validation commands and CI/check outcomes.
- Control-mode status readback proving kernel disabled by default and enabled
  only when configured.
- Management NL passphrase redaction evidence.
- Runtime repair smoke evidence proving heartbeat freshness recovery.
