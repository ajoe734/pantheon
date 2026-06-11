# Closeout: DATASTRAT-MARKETDATA-TW-FINMIND-004

Owner: Codex
Reviewer: Claude2
Date: 2026-06-11
Status: owner finalization prepared

## Delivered Scope

FinMind is integrated as a low-cost Taiwan research-market data provider for
price, chip, shareholding, news metadata, broker daily-report, and SponsorPro
bulk-backfill manifest coverage. The implementation keeps TWSE/TPEx as the
official reference source, Yahoo Taiwan as public fallback where applicable,
and TEJ as paid historical backfill rather than replacing those sources.

Delivered artifacts:

- `services/source_ingestion/connectors/finmind_taiwan.py`
- `services/source_ingestion/tests/test_finmind_taiwan_connectors.py`
- `services/source_ingestion/test_service.py`
- `docs/04/pantheon_data_strategy_source_design_2026-06-09/HANDOFF_DATASTRAT_TW_FINMIND_004.md`
- `docs/04/pantheon_data_strategy_source_design_2026-06-09/REVIEW_DATASTRAT_TW_FINMIND_004.md`

## Review Record

Claude2 approved the task on 2026-06-11 in
`REVIEW_DATASTRAT_TW_FINMIND_004.md`. The review confirms:

- dataset coverage and catalog projection are present;
- `FinMindLiveFetcher` resolves `env://FINMIND_API_TOKEN` at call time;
- token material is sent only through `Authorization: Bearer <token>`;
- raw tokens and signed URLs are excluded from evidence records;
- FinMind does not replace official Taiwan reference truth or TEJ backfill;
- archive-tier symbols remain limited to price baseline fetches.

Implementation PRs already merged to `dev`:

- PR #1297, merge commit `bc13ab8278e49471113da3c3d3fb428c2f0a1ac5`
- PR #1310, merge commit `c463e2afcf72f7677f5d3cdf742f4dd7458d4cc4`

## Final Verification

Focused verification was re-run after merging latest `origin/dev` into the
task branch:

```bash
pytest services/source_ingestion/tests/test_finmind_taiwan_connectors.py
```

Result: 26 passed in 2.39s.

```bash
pytest services/source_ingestion/test_service.py::test_registry_exposes_connector_status_policy_and_provider_examples
```

Result: 1 passed in 1.40s.

## Closeout Notes

No live `FINMIND_API_TOKEN` was installed and no live FinMind smoke was run.
This task closes the fixture-backed provider integration, entitlement metadata,
credential handling, and source-priority documentation. Live credentialed
provider proof remains a separate runtime/ops activity.
