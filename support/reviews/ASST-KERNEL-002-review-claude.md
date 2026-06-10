# Review: ASST-KERNEL-002 — Implement assistant redaction library

Reviewer: Claude
Date: 2026-05-31
Task owner: Codex2
Status: APPROVED

## Artifacts reviewed

- `services/control-plane/bff/assistant/redaction.py`
- `services/control-plane/bff/assistant/tests/test_redaction.py`
- `services/openclaw-gateway-adapter/assistant_provider_runtime.py`
- `services/openclaw-gateway-adapter/tests/test_assistant_provider_runtime.py`
- `docs/04/pantheon_assistant_kernel_user_2026-05-31/ASST_KERNEL_002_REDACTION_IMPLEMENTATION.md`

## Verification run

```
python3 -m pytest services/control-plane/bff/assistant/tests/test_redaction.py \
  services/openclaw-gateway-adapter/tests/test_assistant_provider_runtime.py -v
```

Result: **8 passed in 0.73s**

## Acceptance criteria evaluation

1. **Redacts bearer tokens, cookies, API keys, and private keys** — PASS
   - `_BEARER_RE` catches `Bearer <token>` in text blobs.
   - `_RAW_COOKIE_HEADER_RE` + `_COOKIE_VALUE_RE` cover raw Cookie headers and `name=value` cookie pairs.
   - `_API_KEY_LINE_RE` covers `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `x-api-key` assignment patterns.
   - `_PRIVATE_KEY_RE` (DOTALL) covers full PEM private key blocks.
   - Key-based redaction in `_SENSITIVE_KEY_PATTERNS` catches matching JSON dict keys directly.

2. **Redacts env values, database credentials, provider sessions, and broker credentials** — PASS
   - `_ENV_SECRET_LINE_RE` catches uppercase env var lines with sensitive suffixes (`SECRET`, `TOKEN`, `PASSWORD`, `API_KEY`, `PRIVATE_KEY`, `BROKER_KEY`, `BROKER_SECRET`).
   - `_DATABASE_URL_RE` redacts the user:password portion of DSN URIs while preserving host and path.
   - `_PROVIDER_SESSION_PATH_RE` redacts `.codex` and `.claude` credential mount paths.
   - `_BROKER_LINE_RE` catches `broker_credential`, `broker_secret`, `shioaji_key`, etc.

3. **Preserves diagnostic shape with redacted markers** — PASS
   - `_redact_value` recursively traverses dict / list / str, preserving structure.
   - Stable markers (`[REDACTED_TOKEN]`, `[REDACTED_API_KEY]`, etc.) allow downstream parsing without exposing secrets.
   - `RedactionSummary` tracks per-category counts for telemetry.

4. **User mode fails closed on redaction failure** — PASS
   - `redact_assistant_payload` catches all exceptions and re-raises as `RedactionError` for user mode and kernel mode without override.
   - `AssistantProviderRuntime._redact` wraps `RedactionError` into `AssistantProviderRuntimeError`, preventing the provider from being called.
   - Test `test_user_mode_redaction_failure_fails_closed_before_provider_call` verifies the runner is never invoked when redaction fails.
   - Test `test_transcript_redaction_failure_does_not_persist` verifies the transcript sink is never called when transcript redaction fails.

5. **Tests cover representative secret patterns** — PASS
   - `test_redact_text_covers_representative_secret_patterns`: bearer token, cookie, API key, database URL, provider path, broker secret, account number, PEM block — all in one blob.
   - `test_redact_payload_preserves_structured_shape_and_counts_fields`: nested dict/list with Authorization, Cookie, DATABASE_URL, credential_mount_path, and events list.
   - `test_user_mode_fails_closed_on_redaction_failure` / `test_kernel_mode_requires_explicit_redaction_failure_override`: failure semantics for both modes.
   - `test_provider_invocation_and_transcript_are_redacted_before_use`: end-to-end double-boundary verification.
   - `test_kernel_override_suppresses_unredactable_payload_before_provider_call`: kernel fallback envelope shape.

## Design observations (non-blocking)

- Double-redaction boundary (before provider call + before transcript persistence) is correct and matches the spec.
- `_redact_env_line_once` and `_replace_assignment_once` guard against double-replacement artifacts — good defensive measure.
- Key-based redaction normalizes `-` → `_` before pattern matching, which prevents HTTP header casing bypasses.
- The broad `_SECRET_LINE_RE` (matching `token`, `secret`, `password`) is intentionally conservative — acceptable at a fail-safe boundary.
- Account number regex requires ≥ 6 characters — reasonable heuristic to avoid false positives on short numeric fields.

## Scope boundary

Correctly scoped: does not implement routes, session persistence, CLI subprocess execution, command broker allowlists, or credential mount readiness. Non-scope items are owned by ASST-KERNEL-001, ASST-KERNEL-003, and the ASST-OCGW wave.

## Decision

All five acceptance criteria are satisfied. Tests pass. Implementation is correct, conservative, and well-structured. **APPROVED for owner closeout.**
