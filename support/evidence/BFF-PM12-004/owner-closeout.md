# BFF-PM12-004 Owner Closeout

Task: BFF-PM12-004 - GET /bff/management/persona-league table
Owner: Codex2
Reviewer: Claude2
Phase: Sprint BFF-4 / EPIC-BFF-GAP-PM12
Date: 2026-05-23

## Scope Check

Confirmed the approved PM-12 persona league surface is present in the current
worktree:

- `GET /bff/management/persona-league` is registered in
  `services/control-plane/bff/main.py` and requires BFF read-role auth.
- The route returns the canonical `data` / `items` list envelope,
  `page_info`, and `meta` with `composition_sources` plus per-source
  `surfaces`.
- Rows preserve the execute-plans persona list DTO fields and add
  `routePolicy`, `capabilities`, `bindings`, `sessions`, `evaluations`,
  `memory`, `health`, `allowedActions`, and `links`.
- Query support covers `state`, `archetype`, `q`, `page_token`, and
  `page_size`.
- The execute-plans final live wiring route inventory includes
  `GET /bff/management/persona-league`.

No runtime behavior or API contract code was changed during owner closeout.

## Reviewer Approval

Claude2 approved the task in
`support/reviews/BFF-PM12-004-review-claude2.md`, verifying all six acceptance
criteria and recording the focused test evidence.

Implementation PR #442 merged to `dev` at
`331e89a91b1f6415d705c462931fa7475474f3ae`.

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/tests/test_bff_pm12_persona_league.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -q
```

Result: 10 passed, with 3 existing `datetime.utcnow()` deprecation warnings
from `services/control-plane/bff/read_store.py`.
