# LOOP-001-RB Review - Codex

Task: LOOP-001-RB - `/bff/v5/loop-runs` endpoint rebaseline
Owner: Claude2
Reviewer: Codex
Review date: 2026-05-16
Commit reviewed: 43a51a2f
Disposition: approved

## Scope Verified

- `GET /bff/v5/loop-runs`
- `GET /bff/v5/loop-runs/{id}`
- `GET /bff/v5/sentinel/findings`
- `GET /bff/v5/sentinel/findings/{id}`
- `GET /bff/v5/control-room`
- `GET /bff/v5/execution/persona-health`
- `GET /bff/v5/execution/strategy-health`
- `ReadSurfaceStore._backfill_local_contract_defaults`
- `support/evidence/LOOP-001-RB/README.md`

## Findings

No blocking issues found for the LOOP-001-RB behavior.

The incidents snapshot guard in `ReadSurfaceStore._backfill_local_contract_defaults` preserves an explicitly provided `incidents` dataset, including `{}`, before merging fixture-pack defaults. This fixes the reviewed regression where an available-but-empty incidents source could be backfilled with fixture-pack incidents and make `/bff/v5/loop-runs` return non-empty fixture data.

The existing BFF v5 loop/sentinel routes and read-store delegation remain functional in the current worktree. Dedicated fallback stores, missing-source degraded behavior, empty incidents handling, control-room composition, and health route smoke coverage all pass.

## Scope Note

Commit `43a51a2f` also contains two committee-session mode guards in `open_committee_session` / `close_committee_session`. They are outside the LOOP-001-RB evidence narrative, but they did not affect the LOOP review surface; the adjacent ASK-003 committee lifecycle suite still passes. Treat this as a closeout traceability note rather than a LOOP behavior blocker.

## Verification Run

```bash
pytest services/control-plane/bff/test_bff_v5_loop_sentinel_contract.py services/control-plane/bff/test_read_store_loop_sentinel.py -q
# 33 passed in 44.29s

pytest services/control-plane/bff/test_ask_003_committee_lifecycle.py -q
# 29 passed in 37.63s
```

Additional probe:

```bash
python3 -c "import sys; sys.path.insert(0, 'services/control-plane/bff'); import main; spec=main.app.openapi(); op=spec['paths']['/bff/v5/loop-runs']['get']; print(op.get('operationId')); print([(p.get('name'), p.get('in')) for p in op.get('parameters', [])])"
# sem_final_generic_read_alias_bff_v5_loop_runs_get
# [('id', 'query'), ('authorization', 'header')]
```

The OpenAPI probe reflects the existing generic alias implementation for this rebaseline route. It is not blocking for this task because the reviewed endpoint has no task-specific query filters and the runtime contract tests pass.
