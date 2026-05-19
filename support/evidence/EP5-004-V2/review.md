# EP5-004-V2 Review

Reviewer: Claude2
Date: 2026-05-19
Status: approved

## Artifacts Reviewed

- `services/governance/human_gate/signature_lifecycle.py`
- `services/governance/promotion_readiness/revoke_expire.py`
- `tests/governance/test_revoke_expire.py`

## Verification

```
python3 -c "py_compile.compile(signature_lifecycle); py_compile.compile(revoke_expire)"
→ compile OK

pytest tests/governance/test_revoke_expire.py -q
→ 37 passed in 2.82s
```

## Findings

### signature_lifecycle.py — pure lifecycle functions

All eight lifecycle operations implemented correctly:
- `revoke_signature`: marks one sig as revoked, sets provisional `status="pending"`,
  delegates final state to `validate_decision`. Tests confirm it transitions back to
  `blocked` when readiness is not ready.
- `expire_decision`, `withdraw_decision`, `supersede_decision`: clean terminal transitions;
  guard against double-transition; `supersede_decision` validates non-empty
  `superseded_by_id` and records it in metadata.
- `recompute_can_proceed`: no-op for terminal decisions, delegates to `validate_decision` otherwise.
- `update_blocking_reasons`: replaces blocking_reasons tuple, recomputes state.
- `enforce_ttl`: TTL measured from `created_at`; returns unchanged object when within TTL
  or already terminal; `_parse_utc` handles both `%Y-%m-%dT%H:%M:%SZ` and fractional-seconds
  formats.

### revoke_expire.py — store-level wrappers

Thin load/transform/persist wrappers. No logic duplication.

Minor note: `enforce_ttl_for_all` iterates `store._items` directly (private attribute).
Acceptable for in-package use where no public iteration API is exposed, but worth
adding a `HumanGateDecisionStore.all()` method in a follow-up task.

`enforce_ttl_in_store` correctly returns the unchanged object without a redundant
`store.put` when TTL is not exceeded.

### test_revoke_expire.py — 37 tests

Coverage is comprehensive:
- Pure function tests: terminal/active predicates, revoke (5 cases), expire (3), withdraw (3),
  supersede (3), recompute (2), blocking reasons (3), TTL (4).
- Store-level tests: all 8 wrappers, bulk TTL enforcement (skip terminal, expire multiple).
- Edge cases: missing decision_id, already-revoked signature, already-terminal transitions,
  empty `superseded_by_id`, no `created_at`.

## Conclusion

Implementation is correct, tests are thorough, and the pure/store-layer separation
is clean. Approving with the minor follow-up note on the private attribute access.
