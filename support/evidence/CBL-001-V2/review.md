# CBL-001-V2 Review Notes

**Reviewer:** Claude
**Owner:** Codex2
**Review date:** 2026-05-20
**Task:** CapitalBindingLiveReadiness schema (Part C2)

## Artifacts Reviewed

- `services/capital/binding_live/readiness_model.py`
- `tests/capital/test_binding_live_readiness.py`

## Verification Commands Run

```
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/capital/test_binding_live_readiness.py -q
# → 6 passed in 0.58s

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/capital -q
# → 6 passed in 0.63s

PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/capital/binding_live/readiness_model.py tests/capital/test_binding_live_readiness.py
# → compile OK

git diff --check origin/dev...HEAD -- services/capital/binding_live/readiness_model.py tests/capital/test_binding_live_readiness.py
# → diff check OK (no whitespace violations)
```

## Review Findings

**Schema completeness:** All 5 Part C2 subtrees present and correctly shaped:
- `roles` — sponsor_persona, live_owner, risk_owner, operator (frozen dataclass)
- `required_evidence` — 10 required refs, all enforced as non-empty strings
- `controls` — max_budget_pct (0–100), ttl_hours (>0), three boolean flags
- `approval` — dual-gate (risk_owner + operator), restricted to APPROVAL_STATUSES enum
- `result` — can_bind_live bool with blocking_reasons tuple

**Fail-closed invariants:** `CapitalBindingControls.validate_fail_closed()` correctly enforces:
- `revocation_allowed` must be `True`
- `auto_scale_allowed` must be `False`
- `live_order_allowed` must be `False`
These fire at construction time, preventing any live-order-permissive packet from being created.

**Consistency validation:** `validate_consistency()` correctly enforces:
- `can_bind_live=True` requires zero approval blockers and empty `blocking_reasons`
- `can_bind_live=False` requires non-empty `blocking_reasons` that include all approval blockers

**No broker side effects:** Module header and implementation confirm schema/validation only; no runtime mutations.

**Test coverage:**
1. Full round-trip with schema subtrees (`test_c2_readiness_packet_round_trips_schema_subtrees`)
2. Pending approval → fail-closed result (`test_pending_approval_round_trips_as_fail_closed_result`)
3. `can_bind_live` blocked without dual approval (`test_can_bind_live_fails_closed_without_required_approval`)
4. Blocking_reasons must name approval blockers (`test_closed_result_must_name_pending_approval_blocker`)
5. Controls fail-closed when live_order_allowed=True (`test_can_bind_live_fails_closed_when_live_order_control_enabled`)
6. Missing required_evidence ref raises structural error (`test_missing_required_evidence_ref_is_structural_error`)

## Decision

**APPROVED** — Implementation correctly satisfies all Part C2 requirements. All tests pass. Fail-closed invariants are structural and cannot be bypassed by caller. Returning to owner (Codex2) for finalization.
