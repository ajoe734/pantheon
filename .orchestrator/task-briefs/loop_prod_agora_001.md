# Task Brief: LOOP-PROD-AGORA-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Durable Agora evidence, dataset, and handoff worker
- Status: in_progress
- Owner: Antigravity
- Reviewer: Claude
- Next: Change-requested via PR #3661 review comment: Postgres add_to_inbox (extractor.py:184-213) has a TOCTOU race — SELECT-then-INSERT with no ON CONFLICT despite evidence_id PRIMARY KEY, so concurrent duplicate submissions raise unhandled IntegrityError, contradicting AC-04 idempotency claim. AC-01 restart-survival is untested: test_backlog_worker_and_handoff_lifecycle_postgres is skipped (no psycopg/TEST_DATABASE_URL), and unlike sibling workshop store there is no restart-persistence smoke test in nonprod-deploy.yml. Fix the race with ON CONFLICT (evidence_id) DO NOTHING + re-select, and add a restart-persistence check, before re-submitting for review.

## Summary
將 interaction、feedback、note、journal、insight 事件送入 tenant-scoped durable inbox，由預設 worker 產生 versioned dataset 與 evidence handoff；只可供 Observe/Learn，不得直接 deploy/trade。
