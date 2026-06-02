# Review: ASST-BFF-002 — Add provider option for management NL ask

Reviewer: Claude2
Owner: Codex
Commit: 09cb9353 (PR #771)
Review date: 2026-06-02

## Verdict: APPROVED

All acceptance criteria met. Tests pass 6/6. The implementation is clean and
correctly preserves all pre-existing safety behaviors while adding the provider
option behind a feature flag.

## Acceptance Criteria Assessment

1. **Provider option is feature flagged** ✅
   - `_mgmt_nl_provider_feature_enabled()` at main.py:29838 checks three env
     vars in order: `PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED` →
     `PANTHEON_MGMT_NL_ASSISTANT_PROVIDER_ENABLED` → `PANTHEON_ASSISTANT_ENABLED`.
   - Clean cascade suitable for gradual rollout.

2. **High-risk refusal runs before provider invocation** ✅
   - `_mgmt_nl_high_risk_classify(question)` fires at the top of the route
     handler (main.py:30138), before idempotency check, session creation, audit
     write, or provider call.
   - Returns 403 with `precondition_failed="high_risk_nl_policy"`.
   - `test_high_risk_refusal_runs_before_provider_invocation` confirms
     `fake.calls == []`.

3. **Deterministic synthesis remains fallback** ✅
   - `deterministic_answer` computed unconditionally before provider is tried
     (main.py:30196).
   - `answer = provider_answer or deterministic_answer` (main.py:30282)
     correctly falls back when the provider returns None (disabled, degraded,
     empty output, unsupported provider name).

4. **Answer includes sources, confidence, context pack, and provider status** ✅
   - `data` includes `answer`, `sources`, `confidence`, `contextPack`,
     `providerStatus` with camelCase and snake_case aliases.
   - `meta` mirrors `providerStatus` and `contextPackId`.
   - Context pack is built via `compose_context_pack` in `AssistantMode.USER`
     with sources `["ui", "management_nl"]`; no kernel-only sources.
   - `management_nl` source carries full tenant-scoped management payload.

5. **Tests cover tenant read-role, provider enabled/disabled/degraded** ✅
   - `test_openclaw_client_invokes_codex_provider_contract` — HTTP contract shape
   - `test_provider_disabled_returns_deterministic_answer_and_context_pack` — disabled path
   - `test_provider_enabled_invokes_openclaw_with_tenant_scoped_context` — enabled + tenant-scoped
   - `test_provider_degraded_falls_back_to_deterministic_answer` — degraded (503) fallback
   - `test_provider_enabled_requires_read_role_before_invocation` — 401 before provider
   - `test_high_risk_refusal_runs_before_provider_invocation` — 403 before provider

## Test Evidence

```
pytest tests/test_management_nl_assistant_provider.py -v
6 passed in 4.97s
```

## Additional Notes

- `OpenClawOpsClient.invoke_assistant_provider` validates provider name before
  any network call — correct fail-fast behavior.
- The provider prompt (`_mgmt_nl_provider_prompt`) constrains the model to
  user mode: read-only, no mutation, answer only from context pack.
- `_mgmt_nl_provider_status` sets `fallback: "deterministic_synthesis"` when
  not used and `fallback: null` when provider answers — clean signal for callers.
- `context_composer.py` is well-factored; `management_nl` is properly
  allowlisted and the USER mode policy excludes kernel-only sources.
