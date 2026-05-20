# Review: BLA-008-V2 — Approval Revoke / Withdraw Model

Reviewer: Claude
Date: 2026-05-20
Status: APPROVED

## Scope

`services/broker/live_activation/approval_revoke_withdraw.py`
`tests/broker/test_approval_revoke_withdraw.py`

## Test Results

```
pytest tests/broker/test_approval_revoke_withdraw.py -v  → 6 passed
pytest tests/governance/test_revoke_expire.py -v         → 37 passed
Total: 43 passed
```

## Code Review

**Module design:** Correctly scoped as a pure model layer — no persistence, no runtime dispatch, no broker-live flag mutation. All calls return an explicit `applied/blocked` result.

**Fail-closed contract:**
- Invalid decision input → BLOCKED (no mutation)
- Invalid request input → BLOCKED (no mutation)
- Common validation (action, actor_role, target_role, target_environment) → BLOCKED on any failure
- Cross-role revoke → BLOCKED (`unauthorized_role_scope`)
- Withdraw of approved decision → BLOCKED (`approved_decision_requires_revoke`)
- Non-live environment → BLOCKED (`non_live_human_gate_decision`)

**Immutability:** `HumanGateDecision` and `HumanGateSignature` are frozen dataclasses. Lifecycle helpers return new objects. Original decision is never mutated; tests explicitly verify this (`assert result.updated_decision is decision` on blocked paths).

**Signature selection:** `_select_active_signature` handles exact-match, single-active, no-active, and ambiguous-multiple cases cleanly.

**`_blocked_result`:** Returns the original decision as `updated_decision` on blocked paths — correct semantics (signals no change occurred) and explicitly verified by tests.

**Dual-gate enforcement:** `AUTHORIZED_APPROVAL_ROLES = ("risk_owner", "operator")` and `BROKER_LIVE_ENVIRONMENTS = ("live", "production")` correctly scope the module to the broker live activation dual gate.

**Acceptance criteria check:**
- ✅ risk_owner and operator roles enforced
- ✅ Revoke (signature-level) and withdraw (decision-level) are distinct operations
- ✅ Approved decision requires revoke, not withdraw
- ✅ Cross-role revocation blocked
- ✅ Non-live environment rejected
- ✅ Fail-closed throughout
- ✅ No live broker side effects
- ✅ No L1 canonical docs modified

## Decision

APPROVED. Implementation is clean, well-tested, and correctly implements the fail-closed approval lifecycle model.
