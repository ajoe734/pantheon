# BFF-CONSOL-026 Closeout Evidence

Task: BFF-CONSOL-026
Owner: Codex2
Reviewer: Codex
Closeout date: 2026-05-13

## Approved Scope

Codex approved the fail-hard route diff gate in `.orchestrator/reviews/BFF-CONSOL-026-review-codex.md`.

The approved behavior is present in:

- `scripts/bff_route_diff.py`: default mode is `fail-hard`; active backend-only unmatched routes are included in the failure surface; `fail-but-warn` remains explicitly selectable.
- `.github/workflows/bff-route-diff.yml`: CI invokes `python3 scripts/bff_route_diff.py --check-baseline`.
- `scripts/test_bff_route_diff.py`: tests cover frontend-only failures, backend-only fail-hard failures, fail-but-warn compatibility, family mismatch failures, non-blocking deferred/mock routes, and baseline drift.
- `docs/bff/contract_snapshots/route-diff-baseline.json`: current fail-hard failure surface baseline.
- `docs/bff/contract_snapshots/route-diff-fail-hard-cutover.md`: fail-hard cutover policy and schedule.

Implementation artifacts are already durable in branch history at `34d57b85fcfe4f5dd343925266a27c21b374a545`. That commit is not task-scoped by subject/body, so this closeout adds a narrow BFF-CONSOL-026 evidence commit instead of rewriting shared branch history.

## Final Verification

Commands run during owner closeout:

```bash
python3 scripts/bff_route_diff.py --check-baseline
python3 -m pytest scripts/test_bff_route_diff.py -q
python3 scripts/bff_route_diff.py --dump | jq '.summary'
```

Results:

- `Route diff baseline matches current fail-hard surface.`
- `Grandfathered backend-only routes locked by baseline: 209`
- `7 passed in 2.81s`
- Summary dump: `status=fail`, `failure_count=209`, `backend_missing_frontend=209`, `frontend_missing_backend=0`, `warning_count=0`.

## Worktree Note

The shared worktree has unrelated dirty state/status files and active-task artifacts. This closeout intentionally stages only this BFF-CONSOL-026 evidence file before finalizing the task.
