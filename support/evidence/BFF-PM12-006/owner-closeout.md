# BFF-PM12-006 Owner Closeout

Task: BFF-PM12-006 - GET /bff/management/quarterly-ranking
Owner: Codex2
Reviewer: Claude2
Phase: Sprint BFF-4 / EPIC-BFF-GAP-PM12
Date: 2026-05-23

## Scope Check

Confirmed the approved PM-12 quarterly ranking surface is present in the
current worktree after refreshing the task branch to `origin/dev` at
`9dbc291efc1c1c7cb110a314023ed0615bf3f81a`.

- `GET /bff/management/quarterly-ranking` is registered in
  `services/control-plane/bff/main.py` and requires BFF read-role auth.
- The route accepts `quarter=YYYY-Qn`, computes a UTC quarter window, rejects
  invalid quarter values with HTTP 422, and supports PM-12 persona league
  filters plus pagination.
- The response returns `data`, `items`, `rankings`, `formula`, `quarterWindow`,
  `quarter_window`, `evidenceRefs`, `evidence_refs`, `summary`, `page_info`,
  and `meta`.
- The route composes PM-12 persona league rows, ranking formula metadata,
  quarter-scoped evidence references, source surface status, and
  `read_only_governance_advisory` policy metadata.
- `execute-plans/src/lib/bff-v1/paths.ts` exposes
  `managementQuarterlyRanking()`.
- `execute-plans/src/lib/bff-v1/management.ts` exposes typed quarterly ranking
  query, window, formula, item, summary, response, path, and fetch helpers.
- `services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py`
  includes the route in the final live wiring inventory.

No runtime behavior or API contract code was changed during owner closeout.

## Reviewer Approval

Claude2 approved the task in
`support/reviews/BFF-PM12-006-review-claude2.md`, verifying route composition,
422 invalid-quarter handling, 401 missing-auth handling, route inventory
registration, and typed TypeScript helpers.

Implementation PR #458 merged to `dev` at
`eda6826185b1bcfdb619b2a51fad6adff5869542`.

## Verification

Commands run from `task/BFF-PM12-006` on 2026-05-23:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/control-plane/bff/main.py
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/tests/test_bff_pm12_persona_league.py -v
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -q
```

Results:

- `services/control-plane/bff/main.py` compiled cleanly.
- PM-12 persona-league and quarterly-ranking regression tests: 7 passed in
  4.14s.
- Execute-plans final live wiring contract tests: 7 passed in 3.82s with 3
  existing `datetime.utcnow()` deprecation warnings from
  `services/control-plane/bff/read_store.py`.
- GitHub PR #458 is merged and its visible Branch CI Gate / Orchestrator Sync
  checks are successful.

Additional note: running the route inventory suite with
`PYTHONWARNINGS=error` fails on the existing `read_store.py` UTC deprecation
warning before reaching route behavior. The normal warnings-mode suite passes.
