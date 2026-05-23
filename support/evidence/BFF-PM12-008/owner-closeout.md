# BFF-PM12-008 Owner Closeout

Task: BFF-PM12-008 - GET /bff/management/quarterly-ranking/recommendations
Owner: Codex2
Reviewer: Claude2
Phase: Sprint BFF-4 / EPIC-BFF-GAP-PM12
Date: 2026-05-23

## Scope Check

Confirmed the approved PM-12 quarterly ranking recommendations surface is
present in the current worktree after refreshing the task branch to
`origin/dev` at `cc29749e3c1d5770de532a3136cfc4e54d1503cf`.

- `GET /bff/management/quarterly-ranking/recommendations` is registered in
  `services/control-plane/bff/main.py` and requires BFF read-role auth.
- The route accepts `quarter=YYYY-Qn`, rejects invalid quarter values with the
  shared quarterly-ranking parser behavior, supports PM-12 persona league
  filters, and paginates recommendation rows.
- Recommendations use only the B3.5 governance action allow-list:
  `promote_to_canary_candidate`, `increase_research_budget`,
  `grant_tool_access`, `reduce_capital_access`, `require_retraining`,
  `freeze_persona`, `suspend_persona`, and `retire_persona`.
- Every recommendation is emitted as `recommendationType=governance_advisory`,
  requires `HumanGateDecision`, routes to Human Inbox / Governance Queue /
  HumanGateDecision metadata, and sets `liveCapitalMutation=false`.
- The response returns top-level `items` / `recommendations`,
  `data.recommendations`, `quarterWindow`, `formula`, `evidenceRefs`,
  `summary`, `page_info`, and
  `meta.surfaces.quarterly_ranking_recommendations`.
- `execute-plans/src/lib/bff-v1/paths.ts` exposes
  `managementQuarterlyRankingRecommendations()`.
- `execute-plans/src/lib/bff-v1/management.ts` exposes typed query, item,
  governance, summary, response, path, and fetch helpers.
- The route is included in
  `services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py`.

No runtime behavior or API contract code was changed during owner closeout.

## Reviewer Approval

Claude2 approved the task in orchestrator state at `2026-05-23T10:51:17Z`,
verifying all 6 acceptance criteria: advisory response shape, B3.5 action
allow-list, no live capital mutation, quarter parsing and invalid-quarter
behavior, missing-auth 401 behavior, route inventory registration, and
execute-plans typed helpers.

Implementation PR #473 merged to `dev` at
`f72f0f40da104349beb2eb037e613f6c1f31d0bc`.

Implementation commit:
`49cae63e47f582d836952eaa40f4958ebefd7376`.

## Verification

Commands run from `task/BFF-PM12-008` on 2026-05-23:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/tests/test_bff_pm12_persona_league.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/tests/test_bff_pm12_persona_league.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -q
gh pr view 473 --json number,state,title,headRefName,baseRefName,mergeCommit,mergedAt,url,statusCheckRollup
```

Results:

- BFF main module and focused PM-12 test modules compiled cleanly.
- PM-12 persona-league / quarterly-ranking regression tests plus final live
  wiring contract tests: 16 passed in 7.15s before the first closeout push,
  then 16 passed in 7.34s after refreshing the branch for PR #482's BEHIND
  state.
- The pytest run emitted 3 existing `datetime.utcnow()` deprecation warnings
  from `services/control-plane/bff/read_store.py`.
- GitHub PR #473 is merged into `dev`; visible Branch CI Gate and Orchestrator
  Sync checks on the PR were successful.

## Publication Refresh

The task branch was fast-forwarded to current `origin/dev` before the first
owner closeout commit so the closeout PR composes with later BFF work already
merged after PR #473. PR #482 then reported `BEHIND` after `dev` advanced
again, so the task branch was refreshed with a non-interactive
`origin/dev` merge and this file was updated so the branch tip remains a
BFF-PM12-008 owner commit with the required Codex2 closeout trailers.

- First refreshed dev head: `de1a1701010aa93f794fcf6b947ad364030fa850`
- Second refreshed dev head: `cc29749e3c1d5770de532a3136cfc4e54d1503cf`
- Dev refresh merge commit: `ce1fcd024b6718e961091b00d2b90cba04bb668f`
- Implementation PR merge commit remains an ancestor of `origin/dev`.
- This file records the owner finalization evidence; it does not alter runtime
  behavior or the API contract.
