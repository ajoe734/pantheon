# Assistant User-Mode Contraction

Task: ASST-USER-001
Owner: Claude
Reviewer: Codex2
Date: 2026-06-02
Status: delivered

## Summary

This document records the product-safe user-mode contraction for the Pantheon
assistant.  The assistant now defaults to **user mode** in all environments
unless explicitly enabled for kernel use.

---

## Changes

### 1. Product Default Mode

`mode_policy.py` exports `PRODUCT_DEFAULT_MODE = AssistantMode.USER`.

All session creation requests that omit a `mode` field receive user mode.
The BFF route previously defaulted to `AssistantMode.USER.value`; it now
reads from `PRODUCT_DEFAULT_MODE` so the default is a single authoritative
constant.

### 2. Context Pack Request Default

`AssistantContextPackRequest.mode` now defaults to `AssistantMode.USER`
instead of the prior `AssistantMode.KERNEL_DEBUG`.  Any context pack built
without an explicit mode is in user mode and cannot include kernel-only
sources.

### 3. Kernel Enabled Gate

`mode_policy.assert_kernel_allowed(mode)` is called before capability
checks in the session creation route.  If `PANTHEON_ASSISTANT_KERNEL_ENABLED`
is not set or is not `true`/`1`, any kernel mode request is rejected with
HTTP 403.

Environment default (conservative):

```env
PANTHEON_ASSISTANT_KERNEL_ENABLED=false   # or simply unset
```

To enable kernel sessions on a development/staging environment:

```env
PANTHEON_ASSISTANT_KERNEL_ENABLED=true
```

### 4. User Mode Capability Summary

`mode_policy.user_mode_capability_summary()` returns a machine-readable dict
confirming the product-safe capability set:

```json
{
  "mode": "user",
  "context": "bff_curated_only",
  "command_broker": false,
  "shell": false,
  "repo": false,
  "raw_logs": false,
  "repair": false,
  "provider_session_access": false,
  "allowed_command_classes": []
}
```

This is surfaced through `GET /bff/assistant/mode`.

### 5. BFF Route: `/bff/assistant/mode`

New read-only endpoint that returns the product default mode and whether
kernel sessions are currently enabled.  Useful for frontend feature flag
decisions and for operators verifying the current configuration.

### 5.1 BFF Control-Mode Activation

Frontend control-mode switching is exposed through BFF-owned endpoints, not by
asking the assistant to self-promote:

- `GET /bff/assistant/control-mode` returns the current operator's activation
  state, required role/capability, MFA requirement, and passphrase-change href.
- `POST /bff/assistant/control-mode/activate` activates a short-lived kernel
  mode for the authenticated operator.
- `POST /bff/assistant/control-mode/deactivate` revokes the current activation.
- `POST /bff/assistant/control-mode/passphrase` initializes or changes the
  activation passphrase.

The Management AI chatbox may use the same `/bff/management/nl/ask` endpoint
for control-mode commands; no separate frontend control panel is required. The
BFF intercepts these commands before high-risk classification, context-pack
composition, provider invocation, transcript readback, or audit persistence:

- Type the passphrase alone, for example `九條好漢在一班`, to activate control
  mode when it exactly matches the stored passphrase hash.
- Type an explicit command such as `/control <passphrase>`,
  `/kernel <passphrase>`, `控制模式：<passphrase>`, or `暗號是：<passphrase>`
  to activate and redact even failed passphrase attempts.
- Type `/control status` or `控制模式狀態` to read the current activation state.
- Type `/control off` or `退出控制模式` to revoke the current activation.

Successful chat activation returns the normal Management NL response shape with
`controlMode.active=true`, `controlCommand="activate"`, and
`providerStatus.runtime="management_nl_control_command_interceptor"`.
The returned `question`, Management AI conversation readback, and Management AI
audit events use `[CONTROL MODE COMMAND REDACTED]`; the raw passphrase is not
sent to the assistant provider and is not stored in readback/audit payloads.
For sensitive environments, operators should prefer the explicit `/control
<passphrase>` form because failed explicit attempts are also intercepted and
redacted.

The passphrase is an activation factor only. It never grants RBAC by itself.
Activation also requires:

- `PANTHEON_ASSISTANT_KERNEL_ENABLED=true`
- operator or admin role
- MFA-verified session
- an `assistant.kernel*` capability claim
- non-empty reason
- bounded `ttlSeconds` and `idleTtlSeconds`

The passphrase is stored as a PBKDF2 hash. The minimum length check is measured
in UTF-8 bytes so non-ASCII passphrases can be used. If no passphrase exists, an
admin+MFA operator may initialize one. Once configured, changing it requires the
current passphrase plus a new passphrase.
`PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH` may bootstrap the first hash; once
`PANTHEON_ASSISTANT_CONTROL_MODE_STORE_PATH` contains a rotated hash, the store
file takes precedence on restart.

Management AI ask turns automatically touch the idle timer. If the operator
does not keep interacting before `idleTtlSeconds`, the activation becomes
inactive and subsequent `/bff/management/nl/ask` context packs return to
`mode: "user"`.

### 6. Frontend Path Builders

`execute-plans/src/lib/bff-v1/paths.ts` now includes canonical path builders
for all assistant session routes:

- `assistantSessions()` — POST to create sessions, GET to list
- `assistantSession(id)` — GET session detail
- `assistantSessionContext(id)` — POST to build context pack
- `assistantSessionMessages(id)` — POST to send a message
- `assistantSessionTranscript(id)` — GET transcript
- `assistantSessionRevoke(id)` — POST to revoke session
- `assistantProviders()` — GET provider readiness
- `assistantMode()` — GET product mode and kernel flag

### 7. Ask Personas — User-Mode UI

`AskPersonas.tsx` no longer renders kernel-only controls.  Specifically:

- **Mode badge**: only appears for non-user (kernel) sessions.  User mode
  sessions carry no mode badge.
- **Source citations**: rendered for all modes.  User mode answers cite BFF
  data sources; kernel sessions may cite internal surfaces too.
- **Kernel controls** (TTL, provider status, command-enabled state, audit
  session ID): not rendered for user mode.  The comment `{/* Kernel-only
  controls: not rendered for user mode */}` marks the intentional absence.

---

## User Mode Invariants

| Capability | User Mode |
|---|---|
| Command broker | disabled — empty command class list |
| Shell execution | disabled |
| Repo read/write | disabled |
| Raw log read | disabled |
| Repair workflow | disabled |
| Provider session access | disabled |
| Context sources | BFF-curated allowlist only |
| Kernel-only sources | rejected by `_enforce_mode_policy` |
| Provider Q&A | enabled (via BFF) |
| Source citations | enabled |
| Action suggestions | route through existing BFF command/approval flows |

Control-mode activation does not remove these invariants for inactive or
unauthorized operators. A reviewer with no `assistant.kernel*` capability still
receives user mode, even if they know the passphrase.

---

## Regression Test Coverage

`services/control-plane/bff/assistant/tests/test_user_mode_regression.py`
verifies:

1. `PRODUCT_DEFAULT_MODE == AssistantMode.USER`
2. `AssistantContextPackRequest()` defaults to user mode
3. User mode returns empty command class list
4. `mode_allows_command_broker(user)` is False
5. `user_mode_capability_summary()` disables all kernel controls
6. User mode rejects each kernel-only source (`repo_status`, `sanitized_logs`,
   `health_probes`, `job_logs`) via `_enforce_mode_policy`
7. User mode accepts BFF-curated sources
8. `kernel_sessions_enabled()` returns False when env is unset/false
9. `assert_kernel_allowed()` rejects `kernel_observe/debug/repair` when kernel is disabled
10. `assert_kernel_allowed()` passes for user mode regardless of kernel flag
11. Kernel modes still require capability/reason/TTL when kernel is enabled

`services/control-plane/bff/tests/test_assistant_sessions.py` additionally
verifies the control-mode activation routes: reviewer denial, MFA requirement,
passphrase verification, activation status shape, and passphrase rotation.

`services/control-plane/bff/tests/test_management_nl_assistant_provider.py`
verifies that an active control-mode activation changes the Management AI
context pack mode and actor capabilities seen by the provider.

---

## Rollback

Set `PANTHEON_ASSISTANT_KERNEL_ENABLED=false` (or unset) to disable kernel
mode without a deploy.  The assistant continues to serve user-mode Q&A.
To disable the assistant entirely: `PANTHEON_ASSISTANT_ENABLED=false`.
