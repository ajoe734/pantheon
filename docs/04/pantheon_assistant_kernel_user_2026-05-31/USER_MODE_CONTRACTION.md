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

---

## Rollback

Set `PANTHEON_ASSISTANT_KERNEL_ENABLED=false` (or unset) to disable kernel
mode without a deploy.  The assistant continues to serve user-mode Q&A.
To disable the assistant entirely: `PANTHEON_ASSISTANT_ENABLED=false`.
