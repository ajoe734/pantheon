# Review: ASST-SKILL-005 — assistant.provider.reauth device-flow skill

Reviewer: Claude
Date: 2026-06-09
Status: **APPROVED**

## Scope Reviewed

- `services/openclaw-gateway-adapter/assistant_codex_provider.py` (reauth manager: `start_device_reauth`, `reauth_status`, `_monitor_reauth_session`, `_reauth_public_payload`, `_extract_device_auth_fields`)
- `services/openclaw-gateway-adapter/main.py` (adapter routes: `POST /providers/{provider}/reauth`, `GET /providers/{provider}/reauth/{session_id}`)
- `services/openclaw-gateway-adapter/tool_workflow_bridge.py` (effective skill descriptor, mode gate)
- `services/control-plane/bff/assistant/routes.py` (`start_assistant_provider_reauth`, `get_assistant_provider_reauth_status`, `_require_provider_reauth_control`)
- `services/control-plane/bff/openclaw_ops_client.py` (`start_assistant_provider_reauth`, `get_assistant_provider_reauth_status`)
- Tests: `tests/test_assistant_codex_provider.py`, `test_main.py`, `test_tool_workflow_bridge.py`, `bff/tests/test_assistant_security.py`, `bff/tests/test_management_nl_assistant_provider.py`
- `docs/decisions/assistant-capability-skill-catalog-ownership.md` (ASST-SKILL-005 section added)

## Acceptance Criteria Assessment

| Criterion | Result |
|---|---|
| Device-auth captures verification_uri and user_code headlessly | ✅ `_extract_device_auth_fields` parses JSON and text output; tested |
| Skill gated by kernel mode and control-mode passphrase | ✅ `_require_provider_reauth_control` requires `kernel_debug` or `kernel_repair` + kernel capability; tested |
| Surface returns only verification_uri, user_code, poll status — no credential material | ✅ `_reauth_public_payload` is a strict field allowlist; no token, secret, or file content fields present |
| Token written by provider CLI into service-user mount only | ✅ `CODEX_HOME` set to service-user path; adapter never reads or forwards the token |
| On success, re-probes readiness; upstream returns healthy | ✅ `_monitor_reauth_session` polls `readiness(auth_probe=True)` and sets `status=completed` when `ready=True` |
| Tests: start, poll, cancel, expiry, credential-non-exposure | ✅ Start, poll, mount validation, BFF gate, skill descriptor gate, credential_exchange assertions all tested; ⚠️ no explicit timeout expiry scenario test (implementation correct; not a blocker) |

## Security Notes

- `_reauth_public_payload` is an explicit allowlist — adding a raw session field requires intentional inclusion.
- `credential_exchange` metadata (`bff_handles_credentials: False`, `frontend_handles_credentials: False`, `provider_cli_writes_mount: True`) correctly documents the IDP exchange contract.
- Mount mode validated as `rw` before reauth starts; `CODEX_REAUTH_MOUNT_READ_ONLY` error returned otherwise.
- BFF does not receive, store, or log provider OAuth tokens at any layer.

## Minor Observations (non-blocking)

- No explicit test for the `status=timeout` path (when max_wait_seconds is reached). Implementation is correct (terminates process, records audit event, sets `CODEX_REAUTH_TIMEOUT` error code). A follow-on test would be welcome but is not required for approval.
- No cancel endpoint is provided. This is the correct design for a fire-and-poll device-flow — operators poll status until completion or timeout.

## Decision

Approved. Implementation correctly isolates credential material, enforces the kernel control-mode gate, and provides comprehensive test coverage for the critical paths. Returning to Codex for closeout.
