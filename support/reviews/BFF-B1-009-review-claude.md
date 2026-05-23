# Review: BFF-B1-009 — Confirm-token lifecycle 5 endpoints

**Reviewer:** Claude
**Owner:** Codex2
**Review date:** 2026-05-23
**Commit reviewed:** d881a02a

---

## Verdict: APPROVED

All six acceptance criteria are satisfied. Tests pass. No blocking issues.

---

## Acceptance Criteria Verification

| # | Criterion | Verified |
|---|---|---|
| 1 | `POST /bff/confirm-tokens` issues a token and returns `data.tokenId` / `data.status=created` | ✅ line 24576; test asserts `status==created` and stable `tokenId` field |
| 2 | `GET /bff/confirm-tokens/{tokenId}` returns the current token lifecycle state | ✅ line 24623; returns `_confirm_token_lifecycle_payload` with status+expired+expiresAt |
| 3 | `POST /bff/confirm-tokens/{tokenId}/redeem` marks the token redeemed and preserves the command receipt | ✅ line 24634; calls `_confirm_token_lifecycle_response` with `status=redeemed, redeemed=True` |
| 4 | `POST /bff/command-confirmations` mirrors the lifecycle by marking the token redeemed | ✅ line 22855; accepts `confirm_token`/`confirmToken`/`tokenId`/`token` aliases; writes `CONFIRM_TOKEN_REDEEM` record |
| 5 | `GET /bff/command-confirmations/{token}` returns the mirrored confirmation lifecycle state | ✅ line 22955; aggregates token state + latest confirmation payload |
| 6 | Expired issued tokens return typed HTTP 410 (`INVALID_STATE`, `confirm_token_expired`) on read/redeem/confirmation paths | ✅ `_raise_if_confirm_token_expired` called on all 4 paths; test_expired_confirm_tokens_return_typed_410 covers each |

---

## Test Results

```
python3 -m pytest services/control-plane/bff/tests/test_confirm_token_lifecycle.py -v
2 passed in 2.34s

python3 -m pytest services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_contract_paths_are_registered services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_openapi_json_is_route_discoverable -q
2 passed in 2.32s

python3 -m pytest services/control-plane/bff/test_final_command_execution_bridge.py::test_confirm_token_create_read_redeem_delete_are_command_store_backed services/control-plane/bff/test_final_command_execution_bridge.py::test_confirm_token_server_generated_id_replays_on_same_key_retry -q
2 passed in 2.02s

python3 -m pytest services/control-plane/bff/test_bff_governance_runtime_risk_audit_contract.py::test_bff_governance_review_routes_and_approval_evidence -q
1 passed in 1.83s
```

---

## Implementation Notes

- `_confirm_token_lifecycle_payload` correctly handles `created → expired` transition based on `expiresAt` vs `datetime.now(utc)`.
- Server-generated token ID replay is handled (recovers original tokenId from command store on idempotent retry).
- The `POST /bff/command-confirmations` idempotency uses a private `_GOV_BFF_IDEMPOTENCY` dict with request_hash guard, consistent with other governance routes.
- Commit trailers are correct: `LLM-Agent: Codex2`, `Task-ID: BFF-B1-009`, `Reviewer: Claude`.
- PR #419 merged successfully into dev.

---

## Follow-up (non-blocking)

None. Implementation is complete and clean for the specified scope.
