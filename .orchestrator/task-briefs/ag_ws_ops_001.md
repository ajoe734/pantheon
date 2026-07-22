# Task Brief: AG-WS-OPS-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Durable Workshop versions and selection
- Status: review_approved
- Owner: Codex2
- Reviewer: Claude
- Next: Independent review passed: reran 120+2-skip suite and Postgres restart tests locally (4 passed, 0 residual schemas); verified digest write-once, deterministic ETag-stable backfill, CAS conflict leaves pointers unchanged, tenant isolation before Registry access, and hash-locked additive v1.10 chain over frozen v1.9. Approved and returned to owner Codex2 for PR + closeout.

## Summary
實作 workshop versions list/create/select 三條 deferred API，含 durable StrategySpec version、lineage、idempotency、ETag CAS、tenant isolation 與 restart persistence。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Implementation handoff

- `7655572be` adds durable WorkshopVersionLink identity, parent lineage,
  write-once canonical StrategySpec digest, deterministic legacy active-version
  backfill, tenant/user Registry readback checks, selected-pointer persistence,
  and focused route/store tests.
- `a7959bbac` adds the v1.10 contract extension without rewriting frozen
  v1.2/v1.8/v1.9 bytes: typed list/create/select resources, capability manifest,
  OpenAPI, exact-byte bundle hashes, and contract validation.
- Strategy Registry remains the sole StrategySpec JSON authority. Workshop
  persistence stores only references/projection metadata. Research,
  consultation, conclusion, deployment, order routing, lifecycle promotion,
  and live-capital effects are unchanged.

## Acceptance evidence

- List/create/select return live typed responses; existing no-501 route tests
  remain green.
- Exact create replay returns the same receipt/version; changed payload under
  the same key conflicts before another Registry create.
- Stale selection ETag returns typed `CONCURRENT_MODIFICATION` and leaves both
  selected pointer and lock version unchanged.
- Cross-tenant list/create/select scope is denied before Registry access.
- A changed Registry document under an existing immutable registry/version id
  returns `WORKSHOP_VERSION_PROJECTION_CONFLICT` rather than changing the
  persisted digest.
- Legacy active Registry pointers receive one deterministic version link while
  preserving workshop lock version, created/updated timestamps, and all
  non-additive session fields.
- Reconstructing `PostgresWorkshopStore` against the same isolated schema reads
  both persisted version rows/digests and the selected/active pointers; the
  test finalizer removes the schema and the post-run residual count was zero.

## Verification

```text
/home/lupin/pantheon/.venv/bin/python -m compileall -q services/control-plane/bff/agora/strategy_workshop
/home/lupin/pantheon/.venv/bin/python -m pytest -q services/control-plane/bff/agora/strategy_workshop/test_versions.py services/control-plane/bff/tests/test_agora_workshop_live_operations.py services/control-plane/bff/tests/test_strategy_workshop_command_store.py services/control-plane/bff/tests/test_agora_write_authority.py services/control-plane/bff/tests/test_agora_strategy_workshop.py scripts/test_agora_v1_8_bundle.py scripts/test_agora_v1_9_bundle.py
# Owner closeout rerun: 120 passed, 2 skipped; skips are opt-in Postgres paths in the no-DSN run.

TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:15432/pantheon /home/lupin/pantheon/.venv/bin/python -m pytest -q services/control-plane/bff/agora/strategy_workshop/test_versions.py
# Owner closeout rerun: 4 passed; isolated agora_ws_ops_* schema count after finalizer: 0.

git diff --check
```

Claude independently repeated the focused no-DSN and Postgres restart suites,
verified the immutable digest, deterministic backfill, CAS no-mutation,
pre-Registry tenant isolation, and additive v1.10 hash chain, then approved the
task for owner PR closeout.
