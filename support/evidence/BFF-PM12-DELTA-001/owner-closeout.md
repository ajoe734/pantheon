# BFF-PM12-DELTA-001 Owner Closeout Evidence

Task-ID: BFF-PM12-DELTA-001
Owner: Codex2
Reviewer: Claude2
Phase: Sprint BFF-DELTA / EPIC-BFF-DELTA-PM12-SUB
Closed: pending final owner `done` command after merged closeout PR

## Scope

GET `/bff/management/quarterly-ranking/drilldown` for a single PM-12
quarterly ranking persona drilldown. The delivered surface is read-only
governance advisory composition, with backend route logic, focused route
contract tests, and execute-plans typed path/client support.

## Reviewed Delivery

- Implementation PR: #512
- Merge commit: `c18915ebce8c9a65d39bcaf78333a0c336bcea03`
- Implementation commit: `714df8d40d2c7cd636a6d2de77ebd31e7a003745`
- Reviewer approval artifact: `support/reviews/BFF-PM12-DELTA-001-review-claude2.md`

## Acceptance Verification

| Criterion | Status |
|---|---|
| Route registered at `/bff/management/quarterly-ranking/drilldown` | Verified |
| Read-role auth enforced, including unauthenticated HTTP 401 | Verified |
| `personaId` / `persona_id` is required, with missing input rejected | Verified |
| Unknown persona returns HTTP 404 | Verified |
| Correlation ID header is echoed | Verified |
| Contribution breakdown includes pnl, risk, execution, and activity | Verified |
| Contribution shares stay within `[0, 1]` | Verified |
| Response exposes policy, surface status, and composition sources | Verified |
| execute-plans TypeScript path, interfaces, and fetch helper match backend output | Verified |

## Owner Verification

Commands run from `task/BFF-PM12-DELTA-001` on 2026-05-24:

```bash
pytest services/control-plane/bff/test_bff_management_delta_routes.py -q
# 5 passed in 2.85s

pytest services/control-plane/bff/tests/test_bff_pm12_persona_league.py services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py -q
# 27 passed in 7.44s

python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/test_bff_management_delta_routes.py
# passed

git diff --check
# passed
```

## Closeout Notes

- No L1 canonical architecture or policy document was changed.
- PR #512 was already merged into `dev` before owner closeout.
- A final owner closeout commit is required because the local task branch was
  refreshed to current `origin/dev`; the branch tip must again carry the
  BFF-PM12-DELTA-001 task subject and required Codex2 closeout trailers for
  the `done` gate.
