# Task Brief: LOOP-AUTO-SRC-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Implement source provisioning reconciler
- Status: review_approved
- Owner: Codex2
- Reviewer: Claude
- Next: Review approved: idempotent reconciler satisfies all acceptance criteria; 54 tests passed; returning to owner Codex2 for closeout

## Summary
實作 persona required_data_sources 到 source connector 註冊與 schedule 建立的 idempotent reconciler。

## Closeout Notes

- PR: https://github.com/ajoe734/pantheon/pull/2435
- Refreshed task branch with `origin/dev` at `438d5d93` before owner closeout.
- Local closeout verification:
  `pytest -q services/source_ingestion/tests/test_persona_source_reconciler.py services/source_ingestion/tests/test_scheduled_connector.py services/source_ingestion/tests/test_connector_framework.py services/source_ingestion/tests/test_financial_source_catalog.py services/control-plane/persona/test_persona_data_sources.py`
- Result: `54 passed in 19.77s`.
