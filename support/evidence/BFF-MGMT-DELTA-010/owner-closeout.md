# BFF-MGMT-DELTA-010 Owner Closeout

Task: GET /bff/management/loop-throughput
Owner: Codex2
Reviewer: Codex
Closeout date: 2026-05-24

## Delivered Scope

- Added the read-only `GET /bff/management/loop-throughput` FastAPI route.
- Composed Management Console loop throughput from the existing v5 loop-run
  read surface, with incident-derived loop runs used only as a fallback source.
- Preserved the no-new-source-of-truth boundary; the route does not mutate loop
  runs, incidents, approvals, capital, deployments, or runtime bindings.
- Returned the canonical aggregate envelope with `data`, `items`, `rows`,
  `loops`, `summary`, `metrics`, `page_info`, and `meta`.
- Reported loop count, queue depth, active/completed/failed counts, runs per
  minute, completed runs per minute, observed window, queue lag, and status
  buckets.
- Added execute-plans typed query, response, path, and fetch helper wiring.

## Review

Codex approved the implementation after verifying PR #548 was merged and the
route remained present on latest `origin/dev`.

Implementation commit reviewed before owner closeout:

- `a83b1eb4` - add loop throughput

Implementation PR #548 merged into `dev` at:

```text
0fa0593d6811c44a052e9517283b5c06fdcca935
```

Reviewer verification recorded in task state:

```text
git diff --check 0fa0593d6811c44a052e9517283b5c06fdcca935^1 0fa0593d6811c44a052e9517283b5c06fdcca935
python3 -m pytest services/control-plane/bff/test_bff_management_delta_routes.py services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py -q
```

Result:

```text
93 passed, 3 existing read_store.py datetime.utcnow warnings
```

## Owner Verification

Owner closeout re-read the task brief and touched artifacts, then fast-forwarded
the task branch to current `origin/dev`:

```text
69610bc6c62acadfdc488c087910483422fd66e6
```

Commands run from `task/BFF-MGMT-DELTA-010` on 2026-05-24:

```bash
git diff --check
python3 -m pytest services/control-plane/bff/test_bff_management_delta_routes.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -q
```

Result:

```text
git diff --check exited 0
34 passed, 3 existing read_store.py datetime.utcnow warnings
```

## Closeout Notes

- `meta.policy` remains `read_only_loop_throughput`.
- Anonymous requests return HTTP 401; authenticated requests return HTTP 200.
- CORS preflight and OpenAPI path registration are covered by the focused
  management delta tests.
- Query filters remain `status`, `runtime_id`, `page_token`, and `page_size`.
- This closeout artifact must merge through the task PR before
  `AI_NAME=Codex2 ./scripts/ai-status.sh done BFF-MGMT-DELTA-010 ...` is run.
