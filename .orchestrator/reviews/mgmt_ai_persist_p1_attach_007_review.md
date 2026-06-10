# Review: MGMT-AI-PERSIST-P1-ATTACH-007

Reviewer: Claude2
Date: 2026-06-04
Commit: b0ff881c (PR #921, merged)

## Verdict: APPROVED

## Scope Checked

Task: Forward Management AI image attachments from durable attachment store into OpenClaw/Codex provider invocation payloads as multimodal `image_url` content.

GAP Spec §6 requirement: "Forward to OpenClaw/Codex as a multimodal payload (`image_url` or base64 per provider need) when calling the provider."

Implementation fully satisfies this requirement.

## Files Reviewed

- `services/control-plane/bff/main.py` — BFF multimodal attachment helpers + provider invoke flow
- `services/control-plane/bff/openclaw_ops_client.py` — client `messages`/`attachments` kwargs
- `services/openclaw-gateway-adapter/main.py` — adapter Pydantic model extension
- `services/openclaw-gateway-adapter/assistant_provider_runtime.py` — runtime dataclass + payload pass-through
- `services/control-plane/bff/test_bff_mgmt_ai_persistence_2026_06_03.py` — E2E acceptance tests
- `services/control-plane/bff/tests/test_management_nl_assistant_provider.py` — client unit test
- `services/openclaw-gateway-adapter/test_main.py` — adapter unit test

## Observations

**Correctness:**
- `_mgmt_nl_multimodal_attachment_payload()` correctly reads bytes from the existing ATTACH-006 object store (`store.find_attachment` + `store.read_attachment`), builds `data:{mime};base64,{b64}` URLs — matches `image_url` spec.
- Multimodal provider whitelist (`codex`, `codex_cli`) is explicit and conservative. Text-only fallback for all other providers is correct per spec scope.
- Three-layer fallback: (1) skip if provider not in whitelist, (2) try multimodal if supported, (3) retry text-only if provider returns unsupported-multimodal error. Logic is sound.
- `_provider_failure` helper correctly deduplicates the error recording/status-building that was previously duplicated inline.
- `BLE001` noqa on broad except in attachment fetch is appropriate — provider flow must degrade, not fail.

**Security:**
- Data URLs are assembled server-side from stored bytes; no storage credentials or internal paths are leaked to FE.
- No base64 is stored in the DB (confirmed by test assertion: `assert encoded not in stored_dump`).

**Test coverage:**
- E2E Codex path: `test_multimodal_image_attachment_is_forwarded_to_codex_provider` — asserts message structure, data URL content, attachment metadata, no base64 in DB.
- Text-only fallback: `test_multimodal_attachment_falls_back_to_text_only_for_unsupported_provider` — asserts `providerStatus.reason == "multimodal_unsupported"`, no `messages`/`attachments` in call.
- Client body: `test_openclaw_client_forwards_codex_multimodal_body` — asserts `messages` and `attachments` are present in HTTP request body.
- Adapter pass-through: `test_assistant_codex_invoke_preserves_multimodal_payload` — asserts runtime request carries both fields.

**Minor note (non-blocking):**
The "provider returns multimodal-unsupported error → text-only retry" code path in `_mgmt_nl_maybe_provider_answer` is not covered by a dedicated test, but the logic is short and the happy-path / pre-check fallback tests cover the surrounding machinery. Acceptable for this delivery.

## Summary

Implementation is correct, secure, well-tested, and fully aligned with GAP Spec §6 and the ATTACH-007 task scope. Approving for owner closeout.
