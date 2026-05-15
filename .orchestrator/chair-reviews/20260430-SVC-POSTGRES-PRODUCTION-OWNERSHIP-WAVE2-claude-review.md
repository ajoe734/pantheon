# Review: SVC-POSTGRES-PRODUCTION-OWNERSHIP-WAVE2

Reviewer: Claude
Date: 2026-04-30
Outcome: approved

## Summary

Implementation commit `1eaf381` delivers the full wave 2 Postgres production ownership migration. All acceptance criteria are met.

## Acceptance Criteria Verification

| Criterion | Result |
|---|---|
| Remaining JSONL stores inventoried | `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` §4.1 covers all 8 wave 2 stores |
| Postgres owner stores added for gaps | `PostgresResearchEventStore`, `PostgresWorkerEventStore`, `PostgresPolicyLearningJobStore` added with schema bootstrap DDL; prior pilots (consultation, source-ingest, search, training-session) preserved |
| Staging/prod env selects Postgres without cross-service volumes | `env/prod-control.env.example` sets all 8 backend vars to `postgres` |
| Read-only boundary follows owner API or read-role | Wave 2 inventory documents "owner API or read role only" for all non-owner consumers; service-specific DSN override hooks allow later role separation |
| Dev JSONL/JSON fallback and rollback remain tested | `docker-compose.yml` defaults: `json`, `jsonl`, `jsonl` for the three new stores; tests verify `event_store is None` and `job_store is None` with empty env |
| Focused tests pass | 6/6 passed: `test_research_postgres_event_store.py`, `test_research_worker_gateway_postgres_event_store.py`, `test_policy_learning_postgres_store.py` |

## Code Quality Notes

- PG identifier quoting via `_quote_pg_identifier` prevents SQL injection at the table/schema layer.
- `ON CONFLICT DO NOTHING` for append-only event stores is correct; `ON CONFLICT DO UPDATE` for job stores is correct upsert semantics.
- Bootstrap can be disabled via `*_BOOTSTRAP=0` — useful for ops contexts where DDL is pre-applied.
- The service-specific `*_DSN` env var with `DATABASE_URL` fallback is clean and forward-compatible.

## Verification Commands Run

```
python3 -m pytest \
  services/research/tests/test_research_postgres_event_store.py \
  services/research-worker-gateway/tests/test_research_worker_gateway_postgres_event_store.py \
  services/policy-learning/tests/test_policy_learning_postgres_store.py \
  -v
# Result: 6 passed in 0.14s
```

Also verified:
- All 8 backends set to `postgres` in `env/prod-control.env.example`
- Dev defaults set to `json`/`jsonl` in `docker-compose.yml`
- Wave 2 inventory table complete in `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`
