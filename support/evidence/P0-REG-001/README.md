# P0-REG-001 Evidence

Task: `/bff/strategies` list/detail
Owner: Codex2
Reviewer: Claude
Date: 2026-05-15

## Scope

P0-REG-001 covers the read-only strategy registry surface needed by
execute-plans strict BFF bootstrap:

- `GET /bff/strategies`
- `GET /bff/strategies/{id}`

The current worktree already contains the strategy registry route block in
`services/control-plane/bff/main.py`; this packet records the focused review
and verification for the list/detail slice.

## Implementation References

- `services/control-plane/bff/main.py:17139` projects canonical
  `strategy_specs` records into the execute-plans Strategy DTO.
- `services/control-plane/bff/main.py:17251` merges canonical
  `strategy_specs` with BFF overlay records for list reads.
- `services/control-plane/bff/main.py:17292` serves `GET /bff/strategies`
  with `data`, `items`, `page_info`, and read-surface `meta`.
- `services/control-plane/bff/main.py:17382` serves
  `GET /bff/strategies/{strategy_id}` and returns `OBJECT_NOT_FOUND` for
  missing records.

## Contract Coverage

- `services/control-plane/bff/test_bff_strategy_persona_contract.py:49`
  checks `/bff/strategies` returns the expected envelope and Strategy DTO
  fields.
- `services/control-plane/bff/test_bff_strategy_persona_contract.py:91`
  verifies create/read overlay round-trip for the detail route.
- `services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py:77`
  registers `GET /bff/strategies` and `GET /bff/strategies/{id}` in the final
  execute-plans BFF contract path set.
- `services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py:392`
  verifies seeded list/detail reads return non-generic read-model DTOs.

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_bff_strategy_persona_contract.py -q
# 16 passed in 52.51s

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -q
# 8 passed, 2 warnings in 52.68s
```

The two warnings are existing `datetime.utcnow()` deprecation warnings emitted
from `services/control-plane/bff/read_store.py`.

## Closeout Verification (2026-05-16 — Claude2 finalize)

Finalization re-run confirmed all tests still pass:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_bff_strategy_persona_contract.py -q
# 16 passed in 14.00s

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -q
# 7 passed, 1 failed (out-of-scope), 2 warnings in 12.62s
```

The 1 failure (`test_execute_plans_final_stub_auth_smoke_avoids_server_errors`) is the
P0-CAP-001 fail-closed 503 on `/bff/capital-pools/pool_001`, explicitly noted in the
review as out-of-scope for P0-REG-001.

No isolated task commit was created: `main.py` and `read_store.py` contain
multi-task hunks (P0-PER-001, P0-AUD-001) that cannot be separated non-interactively
in a background worker. Reviewed deliverable (implementation, evidence, review artifact,
and contract tests) was already durable in the repository before closeout.
