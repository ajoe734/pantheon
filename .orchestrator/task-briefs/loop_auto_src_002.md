# Task Brief: LOOP-AUTO-SRC-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Implement source provisioning reconciler
- Status: review
- Owner: Codex
- Reviewer: Copilot
- Next: Codex revalidated the current dev reconciler implementation and is handing off to Copilot for review.

## Summary
實作 persona required_data_sources 到 source connector 註冊與 schedule 建立的 idempotent reconciler。

## Current Codex Handoff Notes

- Re-dispatch reason: `owned_ready_dispatch`.
- Branch: `task/LOOP-AUTO-SRC-002`, fast-forwarded to `origin/dev` at `5160b79b2` before validation.
- Scope: no reconciler code changes in this run; existing dev implementation still provides the store-level `SourceProvisioningReconciler`, API route, idempotent connector/schedule writes, duplicate-tick no-op behavior, and missing connector/schedule repair.
- Verification:
  `pytest -q services/source_ingestion/tests/test_persona_source_reconciler.py services/source_ingestion/tests/test_scheduled_connector.py services/source_ingestion/tests/test_connector_framework.py services/source_ingestion/tests/test_financial_source_catalog.py services/control-plane/persona/test_persona_data_sources.py`
- Result: `54 passed in 14.37s`.
- Review request: confirm the current dev reconciler still satisfies the three acceptance bullets and that no additional source scheduler, SourceHealth, or live-fetch scope is required for this task.

## Historical Closeout Notes

- Original PR: https://github.com/ajoe734/pantheon/pull/2435
- Original owner/reviewer: Codex2 / Claude.
- Prior closeout verification after refreshing with `origin/dev` at `438d5d93`: `54 passed in 19.77s`.
