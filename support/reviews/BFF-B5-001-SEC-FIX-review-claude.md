# Review: BFF-B5-001-SEC-FIX

Reviewer: Claude  
Date: 2026-05-25  
Task: Anti-self-approval + two-man for high-risk HumanGate + extend_ttl cap + revoke fail-closed  
Commit: e17e84a8ea84e60aacb820dd6ebfcdc25176836f  
PR: #600 (merged into dev at 2026-05-25T14:01:53Z)

## Verdict: APPROVED

All four security controls are correctly implemented, tested, and scoped to the declared layer.

## Security Control Verification

### 1. Anti-self-approval
- `_HUMAN_GATE_SELF_APPROVAL_DECISIONS = {"approve", "reject", "revoke"}` — correctly excludes `request_more_evidence` and `extend_ttl` (administrative actions, not decisions).
- `_require_human_gate_security_preconditions` checks `identity.operator_id in requester_ids` and returns 403 `HUMAN_GATE_SELF_APPROVAL_FORBIDDEN`.
- Test: `test_human_gate_approve_reject_revoke_forbid_requester_self_decision` covers all three decision types. ✓

### 2. Two-man for high-risk HumanGate
- `_human_gate_requires_two_man` triggers on `risk_level in {"high", "critical"}`, explicit `requires_two_man=True` flag, or `liveCapitalMutation=True`.
- Evidence is recorded in `audit.precondition_evidence.two_man_signature_id` and mirrored into params.
- Action catalog: HumanGateApprove/Reject/Revoke all carry `requires_two_man=True` at `risk_level=HIGH`. ✓
- Tests: `test_high_risk_human_gate_requires_two_man_and_records_evidence` verifies 409 on missing signature and 202 with valid signature + evidence audit record. `test_human_gate_catalog_and_executor_surface_two_man_evidence` verifies catalog metadata and executor two-man passthrough. ✓

### 3. Extend_ttl cap
- `_human_gate_max_ttl_seconds()` reads `PANTHEON_HUMAN_GATE_MAX_TTL_SECONDS` env var (default 604800 = 7 days).
- Validation at line ~4556 returns 422 `HUMAN_GATE_TTL_EXCEEDS_CAP` with `maxTtlSeconds` in details.
- `HumanGateExtendTtl` correctly has `risk_level=LOW`, `requires_two_man=False` in catalog. ✓
- Test: `test_human_gate_extend_ttl_is_capped_by_env` uses `monkeypatch.setenv` to set 3600s cap and verifies rejection at 3601s. ✓

### 4. Revoke fail-closed
- `_human_gate_downstream_effect_executed` checks `downstream_effect_status` in `{"applied", "complete", "completed", "committed", ...}` plus `downstream_effect_at` timestamps.
- Returns 409 `HUMAN_GATE_REVOKE_DOWNSTREAM_EXECUTED` with suggestion pointing to compensating action through downstream authority. ✓
- Requires `source_record` readable before revoke; returns 409 `HUMAN_GATE_SOURCE_NOT_FOUND` if record is missing. ✓
- Test: `test_human_gate_revoke_fails_closed_after_downstream_execution` seeds approval with `downstream_effect_status="executed"` and verifies 409 with correct reason + suggestion text. ✓

## Test Verification

```
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=services/control-plane/bff python3 -m pytest \
  services/control-plane/bff/tests/test_bff_b1_007_security_hardening.py \
  services/control-plane/bff/tests/test_bff_b5_001_security_hardening.py \
  services/control-plane/bff/tests/test_bff_b5_humangate_commands.py -v
```

Result: **15 passed** (5 B1 regression + 6 B5 security + 4 B5 functional)

## Boundary Compliance

- Owned layer clearly declared in commit body: BFF HumanGate command admission security, action catalog metadata, adapter evidence projection.
- Not touching: Human Inbox read composition, PM-12 ranking semantics, live capital execution authority.
- Composes with BFF-B1-007-SEC-FIX (dependency: done) for confirm/approval/two-man foundation.

## No Objections

Implementation is minimal, correctly scoped, and does not introduce regressions in B1 security tests.
