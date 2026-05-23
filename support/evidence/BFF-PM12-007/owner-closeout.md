# BFF-PM12-007 Owner Closeout

Task: BFF-PM12-007 - GET /bff/management/quarterly-ranking/formula
Owner: Codex2
Reviewer: Claude2
Phase: Sprint BFF-4 / EPIC-BFF-GAP-PM12
Date: 2026-05-23

## Scope Check

Confirmed the approved PM-12 quarterly ranking formula surface is present in
the current worktree after the implementation PR merged to `dev`.

- `GET /bff/management/quarterly-ranking/formula` is registered in
  `services/control-plane/bff/main.py` and requires BFF read-role auth.
- The route returns the quarterly ranking formula weights, active version,
  version history, change-control metadata, governance evidence references,
  summary, and source metadata.
- The response includes both camelCase and snake_case aliases where the BFF
  contract exposes compatibility fields, including `versionHistory` /
  `version_history` and `changeControl` / `change_control`.
- The route reports `meta.policy=read_only_governance_advisory`,
  `meta.surfaces.quarterly_ranking_formula`, and strict-live
  `meta.composition_sources`.
- `execute-plans/src/lib/bff-v1/paths.ts` exposes
  `managementQuarterlyRankingFormula()`.
- `execute-plans/src/lib/bff-v1/management.ts` exposes typed formula version,
  change-control, formula, summary, response, path, and fetch helpers.
- `services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py`
  includes the route in the final live wiring inventory.

No runtime behavior or API contract code was changed during owner closeout.

## Reviewer Approval

Claude2 approved the task in orchestrator state at
`2026-05-23T10:46:31Z`, verifying all 5 acceptance criteria: formula weights,
governance trace, auth enforcement, live wiring registration, and TypeScript
typed helpers.

Implementation PR #471 merged to `dev` at
`b436cb2eaa207c4236171a7828e39dfb2267b8c0`.

Implementation commit:
`94e63b8c90ded08a59974719eebbc1ec7c1aa6c4`.

## Verification

Commands run from `task/BFF-PM12-007` on 2026-05-23:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/tests/test_bff_pm12_persona_league.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/tests/test_bff_pm12_persona_league.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -q
gh pr view 471 --json number,state,title,headRefName,baseRefName,commits,mergeCommit,mergedAt,url
```

Results:

- BFF main module and focused PM-12 test modules compiled cleanly.
- PM-12 persona-league / quarterly-ranking regression tests plus final live
  wiring contract tests: 16 passed in 7.47s.
- The pytest run emitted 3 existing `datetime.utcnow()` deprecation warnings
  from `services/control-plane/bff/read_store.py`.
- GitHub PR #471 is merged into `dev`; visible Branch CI Gate and Orchestrator
  Sync checks on the PR were successful.

## Publication Refresh

Closeout PR #475 initially reported `BEHIND` after `origin/dev` advanced with
BFF-B3-007 work. The task branch was refreshed with `origin/dev` using a
non-interactive merge on 2026-05-23.

- Dev refresh merge commit: `3495e37832a47055d6e449e8eadd9f218499c1d0`
- Refresh source: `origin/dev` at
  `9fbf5098e64b82c03f731a48d960ad4a5f25dcb3`
- Post-refresh verification:
  `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/tests/test_bff_pm12_persona_league.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py`
  passed, and
  `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/tests/test_bff_pm12_persona_league.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -q`
  reported 16 passed in 6.98s with the same 3 existing
  `datetime.utcnow()` deprecation warnings from `read_store.py`.
- This file was updated after the dev refresh so the branch tip remains a
  BFF-PM12-007 owner commit with the required Codex2 closeout trailers.
