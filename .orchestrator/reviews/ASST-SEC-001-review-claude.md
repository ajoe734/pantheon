# Review: ASST-SEC-001 — Add assistant security regression suite

Reviewer: Claude  
Date: 2026-06-02  
Status: **APPROVED**

## Scope

Reviewed four test files delivered as PR #772 (merge `576e1c0a`, impl commit `f1726d34`):

- `services/control-plane/bff/tests/test_assistant_security.py`
- `services/openclaw-gateway-adapter/tests/test_assistant_command_policy.py`
- `services/openclaw-gateway-adapter/tests/test_assistant_credential_mounts.py`
- `services/openclaw-gateway-adapter/tests/test_prompt_injection.py`

Also reviewed the backing implementations:

- `assistant_command_policy.py` — denylist, mode allowlist, audit log (no raw argv)
- `assistant_credential_mounts.py` — service-user path validation, sanitized metadata
- `assistant_provider_runtime.py` — session TTL enforcement, double-redaction boundary

## Acceptance Criteria Coverage

| Criterion | Test(s) | Verdict |
|---|---|---|
| Prompt injection in logs cannot enable commands | `test_prompt_injection_in_logs_cannot_enable_shell_command`, `test_context_pack_redacts_secrets_embedded_in_prompt_injection_logs` | ✅ |
| Env and provider session files excluded from context | `test_context_pack_omits_env_and_provider_session_sources` | ✅ |
| Bearer, cookies, DB URLs, broker credentials, private keys redacted | `test_context_pack_redacts_secrets_embedded_in_prompt_injection_logs`, `test_provider_runtime_redacts_injected_log_secrets_before_runner`, `test_denied_command_audit_omits_raw_secret_argv` | ✅ |
| Denied commands are audited | `test_user_mode_denies_every_command_and_audits`, `test_expired_kernel_session_denies_command_and_audits`, `test_tool_workflow_bridge_requires_openclaw_tool_allowlist`, multiple others | ✅ |
| Kernel TTL expiry prevents provider calls and commands | `test_expired_kernel_session_denies_command_and_audits`, `test_expired_kernel_session_prevents_provider_runner_call` | ✅ |

## Findings

**No blocking issues.**

Positive observations:
- `test_forbidden_human_home_is_rejected_before_stat` uses a `stat_func` sentinel to assert that policy short-circuits *before* touching the filesystem — correct defensive-programming verification.
- `test_denied_command_audit_omits_raw_secret_argv` checks the raw JSONL file content (not just the in-memory object) to confirm no secret leaks at the persistence layer.
- `test_provider_runtime_redacts_injected_log_secrets_before_runner` verifies both the runner payload and the transcript separately — double-boundary coverage.
- `test_expired_kernel_session_prevents_provider_runner_call` asserts `calls == []` — confirms the runner is never invoked on TTL expiry, not merely that an error is raised.
- The `AssistantCommandPolicy` denylist (`_SHELL_HEADS`, `_DB_HEADS`, `_NETWORK_EXFIL_HEADS`, `_DIRECT_BROKER_HEADS`, `_DESTRUCTIVE_HEADS`, sensitive path detection) is comprehensive and well-structured.
- `AssistantCredentialMounts` correctly exposes only opaque `container_target` labels in metadata and never raw host/container paths.

Minor (non-blocking):
- `test_context_pack_omits_env_and_provider_session_sources` asserts several backend keys are `None` but doesn't verify the redaction summary. This is fine as a defense-in-depth check.
- `test_kernel_debug_allows_bounded_diagnostics` checks four command classes together in one loop. Splitting into individual parametrized cases would improve failure isolation, but the current form is readable and acceptable.

## Conclusion

The suite provides solid regression coverage for all five acceptance criteria. Implementation is deny-by-default, the audit log never persists raw secret argv, and the provider runtime enforces session TTL before invoking any runner. Approving for owner finalization.
