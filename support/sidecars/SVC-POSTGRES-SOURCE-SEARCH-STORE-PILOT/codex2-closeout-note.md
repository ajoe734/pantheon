# SVC-POSTGRES-SOURCE-SEARCH-STORE-PILOT Closeout Note

Owner: Codex2
Reviewer: Claude
Date: 2026-04-29
Status: finalized after review approval

## Scope

This closeout finalizes the approved optional Postgres store pilot for
source-ingest and search. JSONL remains the default path. Postgres paths are
enabled only by explicit backend env vars.

## Review Disposition

Claude approved the parent task on 2026-04-29. The approval confirmed:

- JSONL defaults remain intact.
- Postgres activation is env-gated and fails closed without DSNs.
- `source-ingest-svc` remains the source evidence write owner.
- `search-svc` reads source evidence through `PostgresReadOnlyEvidenceRepository`
  and writes only its own search snapshot table.
- The fake-DB Postgres pilot tests and JSONL baseline tests pass.

Review record:
`support/reviews/SVC-POSTGRES-SOURCE-SEARCH-STORE-PILOT/review-claude-20260429.md`

## Final Verification

Commands run during owner finalization:

```bash
python3 -m pytest services/source_ingestion/test_postgres_store.py services/search/test_postgres_store.py
python3 -m pytest services/source_ingestion/test_service.py services/source_ingestion/tests/test_ingest_run.py services/search/tests/test_http_service.py services/search/tests/test_contracts.py services/source_ingestion/test_compose_activation.py services/search/tests/test_service_activation_contract.py
docker compose config --quiet
git diff --check -- services/source_ingestion services/search services/knowledge/evidence/repository.py docker-compose.yml POSTGRES_SOURCE_SEARCH_STORE_PILOT.md support/reviews/SVC-POSTGRES-SOURCE-SEARCH-STORE-PILOT support/sidecars/SVC-POSTGRES-SOURCE-SEARCH-STORE-PILOT
```

Results:

- 16 Postgres pilot tests passed.
- 28 JSONL/default source-ingest and search tests passed.
- Compose config passed.
- Scoped diff check passed.

## Dirty Worktree Separation

The worktree contains unrelated modified and untracked files from other active
tasks. The task-scoped closeout commit stages only the source/search Postgres
pilot implementation, task documentation, and review/closeout records.
