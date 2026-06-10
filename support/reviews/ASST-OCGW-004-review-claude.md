# Review: ASST-OCGW-004 — Implement Claude provider through OpenClaw gateway

Reviewer: Claude  
Date: 2026-06-01  
Status: approved

## Acceptance Criteria Verification

1. **invokes claude inside gateway container using mounted CLAUDE_CONFIG_DIR** ✅
   - `invoke_claude` sets `CLAUDE_CONFIG_DIR` from `_resolve_config_dir(mounts)` using the credential mounts contract's `container_path`.
   - `main.py` passes `_ASSISTANT_MOUNTS` to the invoke endpoint correctly.

2. **normalizes stream-json or text output** ✅
   - `_normalize_output()` handles `result` events, `assistant` content-block events, and plain-text fallback.
   - Mixed-line, empty, and malformed-JSON cases all covered by tests.

3. **missing auth or binary degrades cleanly** ✅
   - Binary not found → `degraded/binary_not_found`.
   - Missing mount → `degraded/auth_mount_missing`.
   - Failed mount → `degraded/auth_mount_{status}`.
   - All return `ClaudeProviderResult`, never raise.

4. **provider uses brokered tool policy not free shell** ✅
   - `--permission-mode plan` is hardcoded; no free shell flags accepted.
   - `tool_policy` parameter reserved for future broker enforcement, not forwarded to subprocess.
   - Test `test_invoke_claude_uses_plan_permission_mode` verifies exact argv.

5. **tests cover ready missing auth timeout and malformed output** ✅
   - 21 tests in provider file, 72 total in full suite.
   - All degraded paths covered; happy path for plain-text and stream-json verified.

## Diff Assessment

- `assistant_claude_provider.py`: Adds `_BINARY_ENV` constant and `_resolve_binary()` to support `PANTHEON_ASSISTANT_CLAUDE_BIN` env override. Binary lookup is consistent with runtime.
- `assistant_provider_runtime.py`: Adds `_PROVIDER_BINARY_ENVS` dict and `_resolve_provider_binary()` to make readiness checks container-configurable.
- No regressions introduced; existing behavior unchanged for default (path-only) invocations.

## Test Run

```
pytest services/openclaw-gateway-adapter/tests/test_assistant_claude_provider.py \
       services/openclaw-gateway-adapter/tests/test_assistant_provider_runtime.py \
       services/openclaw-gateway-adapter/tests/test_assistant_credential_mounts.py \
       services/openclaw-gateway-adapter/test_main.py -q
72 passed in 10.49s
```

## Decision

Approved. All acceptance criteria satisfied. Implementation is correct and minimal.
