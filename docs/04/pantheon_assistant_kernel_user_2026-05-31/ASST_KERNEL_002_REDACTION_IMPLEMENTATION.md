# ASST-KERNEL-002 Redaction Implementation Note

Date: 2026-05-31
Owner: Codex2
Reviewer: Claude
Task: ASST-KERNEL-002

## Scope

This task implements the first assistant redaction boundary for the kernel/user
assistant wave.

Delivered surfaces:

- `services/control-plane/bff/assistant/redaction.py`
- `services/control-plane/bff/assistant/tests/test_redaction.py`
- `services/openclaw-gateway-adapter/assistant_provider_runtime.py`
- `services/openclaw-gateway-adapter/tests/test_assistant_provider_runtime.py`

## Behavior

The BFF redactor recursively processes JSON-like payloads and diagnostic text.
It preserves object/list/string shape while replacing credential-bearing values
with stable markers.

Covered secret classes:

- bearer tokens and raw authorization headers;
- cookies and session IDs;
- API keys and generic env secret values;
- private keys;
- database URLs with embedded credentials;
- provider session paths such as `.codex` and `.claude`;
- broker credentials and account numbers.

`redact_assistant_payload(...)` is mode-aware:

- user mode fails closed on redaction failure;
- kernel modes also fail closed unless the caller provides an explicit override;
- kernel override suppresses the original payload and emits a failure envelope.

The OpenClaw gateway adapter `AssistantProviderRuntime` applies redaction twice:

1. before provider invocation;
2. before transcript persistence.

Provider runners and transcript sinks receive only redacted payloads.

## Non-Scope

This task does not implement assistant routes, session persistence, provider CLI
subprocess execution, command broker allowlists, or credential mount readiness.
Those compose with ASST-KERNEL-001, ASST-KERNEL-003, and the ASST-OCGW task
wave.

## Verification

Focused validation:

```bash
python3 -m pytest services/openclaw-gateway-adapter/tests/test_assistant_provider_runtime.py services/control-plane/bff/assistant/tests/test_redaction.py -q
```

Result: 8 passed.

