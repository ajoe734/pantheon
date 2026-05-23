# BFF-PM12-005 Owner Closeout

Task: BFF-PM12-005 - Persona league rankings and tiers
Owner: Codex2
Reviewer: Claude2
Finalized: 2026-05-23

## Approved Scope

- `GET /bff/management/persona-league/rankings` returns computed ranking blocks from the persona-league source rows.
- `GET /bff/management/persona-league/tiers` returns tier definitions and current season assignments.
- Both routes keep the PM-12 surface read-only and require management read authorization.
- execute-plans exposes typed live fetch helpers and path builders for persona league, rankings, and tiers.

## Review And Merge Evidence

- Review status: approved by Claude2.
- Review note: rankings and tiers routes are correct; score weights, tier definitions, auth guard, and execute-plans helpers satisfy the task.
- PR: #455, `BFF-PM12-005: persona league rankings and tiers`.
- PR state: merged into `dev`.
- Merge commit: `689c400fb2e309c2e947f136ce0f537fd6ae8a14`.
- GitHub checks observed successful: Commit trailers, Runtime mirror guard, Smoke acceptance, Forward to orchestrator.
- Closeout branch refreshed with `origin/dev` at `14cc8cc570f2ee1091ef371848c6bad6851923c4` before publishing this final evidence commit.

## Local Verification

Run from `task/BFF-PM12-005` on 2026-05-23:

```bash
python3 -m py_compile services/control-plane/bff/main.py
pytest -q services/control-plane/bff/tests/test_bff_pm12_persona_league.py
pytest -q services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py
git diff --check
```

Results:

- `py_compile`: passed.
- `test_bff_pm12_persona_league.py`: 7 passed.
- `test_execute_plans_final_live_wiring_contract.py`: 7 passed, 3 existing `datetime.utcnow()` deprecation warnings.
- `git diff --check`: passed.

## Closeout Notes

Quarterly ranking was delivered in the reviewed change as a bonus PM-12 extension and is documented separately in the integration spec. It is not required for BFF-PM12-005 acceptance, and it does not change the read-only PM-12 governance posture.
