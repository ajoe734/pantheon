# AG-GAP-006 — Agora Route Migration

Status: completed; routes migrated out of main.py

## Overview
As part of the Agora gap assessment and structural consolidation, all identity, personalization, shadow, memory, and assistant conversation routes have been migrated out of the legacy monolithic `services/control-plane/bff/main.py` into their respective sub-routers under `services/control-plane/bff/agora/`.

Specifically:
- Identity, sessions, ask, and inbox routes were relocated to `services/control-plane/bff/agora/identity/router.py`.
- Insights, memory, quarantine, and strategy attachment routes were relocated to `services/control-plane/bff/agora/personalization/router.py`.

## Compatibility & Idempotency Preservation
- **Shared Idempotency State**: The compatibility alias `_ASK_SESSIONS_IDEMPOTENCY` was restored in `main.py` referencing `_AGORA_CORE_BFF_IDEMPOTENCY` to ensure existing contract test suites (such as `test_ask_001_sessions_contract.py`) and external callers can clean and inspect the idempotency registry without encountering `AttributeError`.
- **Mocking Integrity**: Refactored the `agora/identity/router.py` to reference `main.OpenClawOpsClient` dynamically instead of using static local scope imports, ensuring unit tests and integration tests can mock out the `OpenClaw` provider correctly.

## Verification
The migrated routes and compatibility aliases are validated using the following test suites, with all tests passing successfully:
- `services/control-plane/bff/test_bff_agora_extended_contract.py`
- `services/control-plane/bff/test_bff_b2_005_agora_canonical_aliases.py`
- `services/control-plane/bff/test_ask_001_sessions_contract.py`
- `services/control-plane/bff/test_bff_agora_core_contract.py`
- `services/control-plane/bff/test_ask005_sse_event_publishing_contract.py`
- `services/control-plane/bff/test_ask_003_committee_lifecycle.py`
- `services/control-plane/bff/test_ask_004_memo_publish_contract.py`
- `services/control-plane/bff/tests/test_assistant_agora_ask.py`
