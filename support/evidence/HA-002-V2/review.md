# Review: HA-002-V2 — SLA targets JSON (Part D3)

Reviewer: Claude
Date: 2026-05-19
Status: approved

## Scope

Reviewed `services/bff/ha/sla_targets.json` and `tests/bff/test_sla_targets.py`
as delivered in commit `1a3c56cb9694292619dc5d296e3ed5a7c77b41d1`.

## Artifact Checks

### services/bff/ha/sla_targets.json

- `schema_version` is `bff_ha_sla_targets.v1` — correct.
- `source` points to the Part D3 supplement anchor — correct citation.
- All three environments present: dev, staging, production.
- All six required fields present in each environment:
  `uptime_target_percent`, `p99_latency_ms`, `sse_connections`,
  `rto_seconds`, `rpo_seconds`, `monthly_cost_ceiling_usd`.
- Values follow expected ordering across environments:
  - Uptime: 95.0 → 99.0 → 99.5% (stricter toward production) ✓
  - p99 latency: 1000 → 700 → 500 ms (stricter toward production) ✓
  - SSE connections: 100 → 500 → 1000 (higher in production) ✓
  - RTO: 300 → 120 → 60 s (stricter toward production) ✓
  - RPO: 60 → 30 → 10 s (stricter toward production) ✓
  - Cost ceiling: 100 → 300 → 800 USD ✓
- RPO ≤ RTO invariant holds for every environment (60≤300, 30≤120, 10≤60) ✓
- JSON is valid (jq parses cleanly).

### tests/bff/test_sla_targets.py

- Three tests cover the required cases:
  1. `test_sla_targets_match_part_d3_values` — happy path, exact value match + source anchor
  2. `test_sla_targets_reject_missing_required_field` — missing field error
  3. `test_sla_targets_reject_invalid_numeric_types` — wrong-type error
- Validator correctly guards against bool-as-int coercion.
- RPO ≤ RTO invariant is checked programmatically in the validator.
- Extra-field guard prevents silent schema drift.
- Path resolution is repo-root-relative using `__file__` — portable.

## Verification

```
python3 -m pytest -q tests/bff/test_sla_targets.py
3 passed in 0.31s
```

## Decision

**Approved.** The SLA target artifact and its validation tests are correct, complete, and match the Part D3 specification. The artifact is ready for alert/dashboard consumer use.
