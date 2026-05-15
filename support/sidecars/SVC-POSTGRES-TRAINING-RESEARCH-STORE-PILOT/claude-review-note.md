# Review Note: SVC-POSTGRES-TRAINING-RESEARCH-STORE-PILOT

Reviewer: Claude
Date: 2026-04-29
Owner: Codex2

## Verification

```
python3 -m pytest services/training-session/tests -v
→ 5 passed in 1.57s

python3 -m py_compile services/training-session/store.py services/training-session/main.py
→ OK
```

## Acceptance Criteria Check

| Criterion | Status | Evidence |
|---|---|---|
| Migration slice names table owner and read contract for each event store | ✅ | `POSTGRES_EVENT_STORE_PILOT.md` table covers training-session, research, policy-learning, research-worker-gateway with write owner and read contract |
| One service lands optional Postgres event-store pilot behind env flag | ✅ | `TRAINING_SESSION_EVENT_STORE_BACKEND=postgres` gates `PostgresTrainingSessionEventStore` in `store.py` |
| JSONL default remains unchanged | ✅ | `build_training_session_store` returns JSONL store when env unset or "jsonl"; confirmed by `test_build_training_session_store_keeps_jsonl_default` |
| Research learning production adapters stay disabled | ✅ | Only training-session has Postgres path; research/policy-learning/research-worker-gateway marked "Planned, not enabled" |
| Compose default unchanged | ✅ | `test_compose_wires_training_session_service_and_bff_normal_path` passes; no Postgres env in compose default |

## Code Quality Notes

- SQL injection protected: `_quote_pg_identifier` validates identifiers with `^[A-Za-z_][A-Za-z0-9_]*$` regex and quotes them; event payload uses `%s` parameterized queries.
- Idempotent: `ON CONFLICT (event_id) DO NOTHING` prevents duplicate event insertion.
- Soft import: `psycopg` imported inside `_connect()` with a clear error message — JSONL path never loads psycopg.
- DDL matches `POSTGRES_EVENT_STORE_PILOT.md` bootstrap section exactly.
- Write boundary respected: only `training-session-svc` writes to `training_session.teaching_events`; reads go through the service API.

## Minor Observation

`psycopg[binary]` is now in `requirements.txt`, so Docker image installs it even for JSONL-only deployments. This is acceptable for a pilot — the binary is never imported unless the env flag is set.

## Conclusion

Approved. Implementation is correct, narrow, and meets all acceptance criteria.
