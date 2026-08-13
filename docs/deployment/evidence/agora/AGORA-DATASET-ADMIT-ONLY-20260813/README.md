# Agora Dataset Extraction: Admit-Only Separation & Leased Workers

**Task ID**: `AGORA-DATASET-ADMIT-ONLY-20260813`  
**Owner**: Antigravity2  
**Reviewer**: Antigravity  

## Architecture & Overview

This change cleanly separates dataset acceptance (`POST /bff/agora/interaction-evidence`) from background extraction and delivery workers:

1. **Admit-Only Persistence (`POST /bff/agora/interaction-evidence`)**:
   - Persists evidence directly into the tenant/user-scoped durable inbox (`agora_evidence_inbox`).
   - Generates an immutable admission receipt (`rcpt-adm-...`) and returns HTTP 201 immediately with `status: "pending"`.
   - Never invokes `process_inbox` or waits for worker completion inline.

2. **Leased Extraction & Delivery Workers (`POST /bff/agora/dataset-worker/process`)**:
   - Claims batches using leased locks (`lease_owner`, `lease_token`, `lease_expires_at`).
   - Validates consent (`consent_granted`), purpose, and retention limits.
   - Enforces raw conversation exclusion and minimization rules (`is_raw_conversation`, `explicit_conversation_consent`).
   - Automatically sanitizes sensitive keys (passwords, tokens, api keys, secrets, authorization headers, credit cards).
   - Atomically commits `DatasetRecord` into `agora_dataset_records` and creates durable handoff in `agora_evidence_handoffs`.
   - Failed claims transition to DLQ with detailed error diagnostics without leaking private payload data into logs.

3. **Single-Item Ordered Handoff Acknowledgement (`POST /bff/agora/dataset-worker/handoffs/{handoff_id}/ack`)**:
   - Acknowledges single handoff rows exactly once with exact `dataset_version_id` matching.
   - Idempotent on repeated calls with matching digest.
   - Supports both string and structured dictionary `downstream_ref` objects from `services/policy-learning/agora_handoff_drainer.py` and `L12-MFC-R4-AGORA-001`.
   - Never bulk-acknowledges historical rows.

4. **Crash Recovery & Tenant Isolation**:
   - Verified crash recovery prior to dataset creation (expired lease reclamation).
   - Verified duplicate replay idempotency across all routes.
   - Enforced strict tenant isolation: cross-tenant read, ack, and DLQ replay are blocked.

## Verification Evidence

- `services/control-plane/bff/agora/dataset_extraction`: 80 passed, 4 skipped in pytest suite.
- `services/policy-learning`: 106 passed, 5 skipped in pytest suite.
