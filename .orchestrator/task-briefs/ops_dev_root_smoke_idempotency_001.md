# Task Brief: OPS-DEV-ROOT-SMOKE-IDEMPOTENCY-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Make hosted source-search smoke repeatable
- Status: review_approved
- Owner: Codex
- Reviewer: Codex2
- Next: Independent review approved: PR #4042 merged to dev at 9b97aa71a16124dbb4577464d68c1d0e4ea23ba1 with Commit trailers, Runtime mirror guard, and Smoke acceptance green. Diff is limited to the two declared artifacts. Reviewer passed py_compile, direct compose contract tests, adapter checks, compose config, and two bounded smokes against the same persistent volumes; readback showed 8 disjoint UUID-scoped connector IDs and both replay connectors at 3 attempts (2 failed, 1 successful), preserving bounded egress, DLQ replay, scheduled frontier, search refresh, and access-filter assertions. Reviewer Compose project and volumes were removed.

## Summary
修正 dev full-root 重跑時 source/search bounded smoke 因固定 connector ID 與持久化 attempt state 而無法再次產生 DLQ 的問題，確保同一持久環境可重複驗證。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Closeout Evidence
- Delivery: Pantheon PR #4042 merged to `dev` at `9b97aa71a16124dbb4577464d68c1d0e4ea23ba1` on 2026-07-24.
- Approved implementation commits:
  - `15ebda66838abc62d19f292db4ffba4444f00e2e` — run-scoped connector identities in the bounded smoke and compose contract assertions.
  - `c7a8e3487e4a10b05020bacd75d2b5927f2f0302` — regression coverage proving two runs use disjoint connector identities.
- Approved scope remained limited to:
  - `scripts/smoke_source_search_bounded.py`
  - `services/source_ingestion/test_compose_activation.py`
- PR checks passed: Commit trailers, Runtime mirror guard, and Smoke acceptance.
- Independent reviewer acceptance ran `py_compile`, direct compose contract tests, adapter checks, compose config, and the bounded smoke twice against the same persistent volumes. The second run again produced a DLQ entry and replayed it successfully; readback showed eight disjoint UUID-scoped connector IDs across the two runs.
- Owner finalization verification:
  - `python3 -m py_compile scripts/smoke_source_search_bounded.py services/source_ingestion/test_compose_activation.py`
  - `docker compose config --quiet`
  - `/tmp/pantheon-pytest-ops-dev-root-smoke-idempotency-001.8PZuHV/bin/python -m pytest -q services/source_ingestion/test_compose_activation.py` (`2 passed`)
- No canonical architecture document changed; this brief is the task-scoped closeout record for the already-approved implementation.
