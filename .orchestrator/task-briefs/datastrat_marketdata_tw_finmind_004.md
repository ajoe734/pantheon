# Task Brief: DATASTRAT-MARKETDATA-TW-FINMIND-004

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: FinMind Taiwan data provider integration
- Status: review
- Owner: Codex
- Reviewer: Claude2
- Next: Codex implementation ready for Claude2 review; see `docs/04/pantheon_data_strategy_source_design_2026-06-09/HANDOFF_DATASTRAT_TW_FINMIND_004.md`.

## Summary
評估並接入 FinMind 作為比 TEJ 更便宜且比官方 API 更整合的台股替代來源，明確標記可覆蓋資料集、授權、頻率、歷史回補限制與備援順位。

## Verification
- `pytest services/source_ingestion/tests/test_finmind_taiwan_connectors.py` -> 26 passed.
- `pytest services/source_ingestion/test_service.py::test_registry_exposes_connector_status_policy_and_provider_examples` -> 1 passed.
- `pytest services/source_ingestion/tests services/source_ingestion/test_service.py` -> 285 passed, 1 skipped.
