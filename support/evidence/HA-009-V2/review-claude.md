# HA-009-V2 Review — Claude

**Reviewer**: Claude
**Date**: 2026-05-20
**Artifact**: tests/bff/test_idempotency_multi_replica.py
**Verdict**: APPROVED

## Review Summary

### Test Design

Both tests share the same `BFF_DATA_DIR` across all 3 replica module instances, correctly
simulating a shared backing store for multi-replica idempotency verification.

### Test 1: `test_idempotency_key_replays_same_response_across_replicas`

- Sends initial POST to replica 0 with an idempotency key → 202 accepted
- Sends exact replay to replica 1 with same key and payload → 202 with identical response body
- Queries replica 2 for command status → verifies `idempotency_key` and `request_hash` fields present
- Checks `command_store._get_all_commands()` returns exactly 1 record with `status == "succeeded"`

Covers: exact replay across replicas returns same response ✅

### Test 2: `test_changed_payload_with_same_key_conflicts_across_replicas`

- Sends initial POST to replica 0 → 202
- Sends changed payload to replica 2 with the same key → 409 with `IDEMPOTENCY_CONFLICT`
- Checks replica 1's store has exactly 1 record, matching the first command's receipt_id

Covers: changed payload with same key returns 409 IDEMPOTENCY_CONFLICT ✅

### CI Validation (per PR #293, merged into dev e2d1786d)

- pytest -q tests/bff/test_idempotency_multi_replica.py: pass (local, confirmed by owner)
- pytest -q tests/bff/test_idempotency_multi_replica.py tests/bff/test_multi_replica_smoke.py: pass
- GitHub checks: Commit trailers ✅ Runtime mirror guard ✅ Smoke acceptance ✅

### Notes

- No live broker or capital side effects
- Private `_get_all_commands()` access is acceptable in test-only context
- The `_noop_process_command` stub correctly prevents async processing in test
