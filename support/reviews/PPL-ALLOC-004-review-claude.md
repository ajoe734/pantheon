# PPL-ALLOC-004 Review — Claude (reviewer)

## Scope reviewed
- `services/control-plane/bff/persona_allocation_policy.py` (new)
- `services/control-plane/bff/main.py` (`/bff/management/allocation-policy/evaluate`, `/bff/rebalances` proposal fields, `/bff/rebalances/{id}/apply`)
- `services/control-plane/bff/read_store.py` (`create_rebalance` proposal persistence)
- `services/control-plane/bff/tests/test_bff_persona_allocation_policy.py`
- `services/control-plane/bff/tests/test_bff_rebalance_proposals.py`

## Verification run
- `python3 -m pytest tests/test_bff_persona_allocation_policy.py tests/test_bff_rebalance_proposals.py -q` → 5 passed
- `python3 -m pytest tests/test_bff_b2_list_detail_facade.py tests/test_bff_path_dedupe.py tests/test_console_data_ooda_projection.py tests/test_bff_capital_pool_bindings.py -q` → 47 passed, 2 failed
  - The 2 failures (`test_bff_path_dedupe.py::test_deprecated_alternate_url_families_return_410_with_headers`,
    `::test_deprecated_nested_action_families_return_410_with_headers`) reproduce identically on `dev` tip
    before this task's commit (checked via a throwaway worktree at `ffe83a8fc`), so they are pre-existing and
    out of scope for this task.

## Finding — blocking

**Quarterly increase-cap smoothing zeroes out every first-time canary/live allocation.**

`calculate_target_allocations` applies:

```python
increase_cap = current * 1.25
if target > increase_cap:
    target = increase_cap
```

Any persona entering `canary_running` or `live_running` for the first time has
`current_weight == 0` (confirmed: `current_weight` is read straight from the
RuntimeBinding/capital-pool record via `_persona_fleet_record_value`, which is
unset/0 before any real capital has been bound). For those rows
`increase_cap = 0 * 1.25 = 0`, so `target_weight` is forced to `0` regardless
of rank score or the stage/tier cap (canary 5%, live S/A/B 25/15/8%).

Reproduced directly against `persona_allocation_policy.calculate_target_allocations`:

```
current_weight=0.0 target_weight=0.0 delta=0.0 cap_reasons=['canary_cap', 'quarterly_increase_cap_25pct']
```

This defeats the acceptance criteria and the feature's purpose: a
paper→canary or canary→live promotion can never receive a positive real
allocation through this policy. Per
`PERSONA_PROMOTION_ALLOCATION_GAP_SPEC.md` §"Caps And Smoothing", the +25%
cap applies "unless explicitly approved as an override" — no override
mechanism exists in the shipped code, so there is no way to bootstrap a
first-time allocation at all.

**Required change:** give first-time entrants (`current_weight == 0`) a
bootstrap path instead of multiplicatively smoothing from zero — e.g. only
apply the `current * 1.25` ceiling when `current > 0`, and let a persona
with `current_weight == 0` receive up to the stage/tier cap directly. Add a
test asserting a fresh canary/live entrant (`current_weight = 0`) receives a
nonzero `target_weight` bounded only by the tier cap.

## Minor (non-blocking, worth a follow-up note)
`_tier_cap` falls through to the live tier-cap dict (`{"s": 0.25, "a": 0.15,
"b": 0.08}`) for any stage other than `canary_running`/frozen/suspended/
retired, including `paper_running`. It happens to be masked today because
`paper_running` rows are always excluded (`stage_not_real_allocation_eligible`)
before the cap is applied, but the `cap_reason` label (`live_s_tier_cap`) is
misleading if that exclusion logic ever changes. Worth an explicit
`paper_running -> 0.0, "paper_not_real_allocation_eligible"` branch for
clarity, not blocking this review.

## Verdict
Reopened — blocking finding above must be fixed before re-review.
