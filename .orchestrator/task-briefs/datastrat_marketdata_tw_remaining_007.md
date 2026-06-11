# Task Brief: DATASTRAT-MARKETDATA-TW-REMAINING-007

This file is task-scoped execution context for the per-task worktree. Treat
`ai-status.json` as durable task state only when updating state through
`scripts/ai-status.sh`.

## Task

- Title: Taiwan remaining gaps: chip finance news storage and throttling
- Status: in_progress
- Owner: Codex
- Reviewer: Claude2
- Phase: Market data completion
- Branch: `task/DATASTRAT-MARKETDATA-TW-REMAINING-007`
- Last update: 2026-06-11T04:37:44Z
- Next: Implement remaining Taiwan market-data gap controls and hand off for review.

## Summary

補齊台股資料完整性缺口：分點只抓 active universe top15/top20、新聞/財報/籌碼分級更新、大量資料 raw retention 與壓縮，以及 archive universe 暫停細節更新。

## Dependencies

- `DATASTRAT-MARKETDATA-TW-PUBLICWEB-003`
- `DATASTRAT-MARKETDATA-TW-OFFICIAL-002`

## Acceptance Criteria

- Core/candidate/archive universe policy is enforced for expensive Taiwan datasets.
- Broker top tables store only top15/top20 unless an explicit backfill mode is used.
- Daily, weekly, and intraday/event cadences are separated in catalog and scheduling policy.
- Raw retention and compression policy is explicit for high-volume Taiwan sources.
- Gap report coverage includes remaining Taiwan TDCC and TAIFEX datasets.
- PR merges to `dev` before task closeout.

## Owned Scope

- `services/source_ingestion/active_universe.py`
- `services/source_ingestion/financial_source_catalog.py`
- `services/source_ingestion/market_data_storage.py`
- `services/source_ingestion/connectors/finmind_taiwan.py`
- `services/source_ingestion/connectors/yahoo_taiwan.py`
- Focused tests under `services/source_ingestion/tests/`
- Closeout evidence under `docs/04/pantheon_data_strategy_source_design_2026-06-09/`

## Non-Scope

- No live TDCC or TAIFEX network fetch is enabled by this task.
- No full-text news storage is enabled by default.
- No broker, order, or capital-affecting path is touched.
- Pending TDCC/TAIFEX provider-owned adapters remain disabled until separately implemented or admitted.

## Verification Log

- `python3 -m py_compile services/source_ingestion/active_universe.py services/source_ingestion/financial_source_catalog.py services/source_ingestion/market_data_storage.py services/source_ingestion/connectors/finmind_taiwan.py services/source_ingestion/connectors/yahoo_taiwan.py`
- `pytest services/source_ingestion/tests/test_active_universe.py services/source_ingestion/tests/test_financial_source_catalog.py services/source_ingestion/tests/test_finmind_taiwan_connectors.py services/source_ingestion/tests/test_yahoo_taiwan_connectors.py services/source_ingestion/tests/test_market_data_foundation.py -q` -> 43 passed.
