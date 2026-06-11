# Task Brief: DATASTRAT-MARKETDATA-TW-MOPS-005

This file is generated for task-scoped execution context and closeout evidence.
Treat `ai-status.json` as durable task state only when explicitly updating state.
Do not read `current-work.md` by default for implementation context.

## Task
- Title: MOPS filings revenue material events connector
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Phase: Market data completion
- Next: Owner closeout after reviewer approval.

## Summary
MOPS is the official-reference source for Taiwan material events, monthly
revenue, financial statements, company master, and supported corporate actions.
TEJ remains a vendor research backfill and must not replace MOPS disclosure
truth. This task completed route-specific normalization plus event and daily
update strategy metadata.

## Acceptance Criteria
- MOPS material events run for core, candidate, and archive symbols.
- MOPS monthly revenue and financial statements run for core symbols.
- Normalized outputs include `tw_material_event`, `tw_monthly_revenue`,
  `tw_financial_statement`, `tw_company_master`, and `tw_corporate_action`
  where supported by routes.
- Financial rows preserve fiscal year, quarter or month, announcement date,
  available time, company id, and raw route id.
- Restatement/correction routes are inventoried and represented in gap reports.
- Live public smoke for one MOPS route passes through existing
  market-data/source-ingest smoke evidence.

## Implementation Record
- Implementation commit: `8b884a795cce4c7b103b205176543d709f1edb7e`.
- Implementation PR: #1296.
- Merge commit: `bc0f3d3bb4311949cbbbeac9659bf5e29aa3a713`.
- Merge target: `dev`.
- GitHub required checks: Commit trailers, Runtime mirror guard, Smoke
  acceptance, and Orchestrator Sync were successful on PR #1296.

## Closeout Verification
- `pytest services/source_ingestion/tests/test_taiwan_market_connectors.py services/source_ingestion/tests/test_active_universe.py services/source_ingestion/tests/test_financial_source_catalog.py`
  passed with 14 tests on 2026-06-11.
- `python3 -m py_compile services/source_ingestion/connectors/taiwan_market.py services/research/adapters/taiwan_market_client.py services/source_ingestion/active_universe.py services/source_ingestion/financial_source_catalog.py`
  passed on 2026-06-11.
- `python3 scripts/run_marketdata_credential_smoke.py --provider mops --allow-network --output-dir /tmp/DATASTRAT-MARKETDATA-TW-MOPS-005-smoke-closeout`
  passed on 2026-06-11; summary status was `pass`, provider `mops` was
  `read_ok`, HTTP status was 200, artifacts were read-only, and no raw secret
  material was present.

## Relevant Files
- `services/research/adapters/taiwan_market_client.py`
- `services/source_ingestion/connectors/taiwan_market.py`
- `services/source_ingestion/active_universe.py`
- `services/source_ingestion/financial_source_catalog.py`
- `services/source_ingestion/tests/test_taiwan_market_connectors.py`
- `services/source_ingestion/tests/test_active_universe.py`
- `services/source_ingestion/tests/test_financial_source_catalog.py`

## Working Rules
- MOPS is official reference truth for Taiwan disclosures.
- Keep polling courteous and bounded.
- Do not infer future availability dates.
