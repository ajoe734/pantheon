# Review: DATASTRAT-CATALOG-003

Reviewer: Claude2
Date: 2026-06-09
Commits reviewed: ce005b96, b48fcd08

## Scope

Add initial financial data-source catalog and active-universe scheduling policy.

## Acceptance Criteria Check

| Criterion | Result |
|---|---|
| Catalog entries for FinMind, TWSE/TPEx, MOPS, Yahoo, SEC EDGAR, FRED | PASS - all 6 providers present in `financial_source_catalog.py` |
| Each entry declares source class, datasets, update frequency, license scope, secret_ref policy | PASS - `DataSourceEntry` templates carry all required fields; config templates use `secret_ref_id` with `env://` prefix only |
| Active universe policy supports core/candidate/archive tiers | PASS - `UniverseTier` enum, `SourceUpdateRule.eligible_tiers`, and `active_universe_policy_payload()` verified |
| Archive tier skips broker top N and detailed news fanout | PASS - `tw-finmind-broker-daily-report`, `tw-finmind-datasets`, `tw-yahoo-stock-rss` are scoped to CORE+CANDIDATE only; TWSE and MOPS material events extend to ARCHIVE |
| Health projection includes last_success_at, watermark, row_count, staleness, source_error | PASS - pre-existing `_connector_health_metrics` in `read_store.py` is unmodified and intact; BFF surfaces these fields |
| No inline credentials accepted or documented | PASS - all auth configs use `secret_ref_id: env://...`; `test_catalog_config_templates_are_secret_ref_only` asserts `"raw-key" not in encoded` |

## Implementation Review

### financial_source_catalog.py

Clean. Catalog entries are correctly tagged `lifecycle_state=candidate` and `template_status=candidate_not_live_ingestion_claim`. DataSourceClass values are well-chosen. Config templates are narrow (no live adapter instantiation). deepcopy in `initial_financial_data_source_config_templates()` is correct for mutable dict templates.

### active_universe.py

Solid. Frozen dataclasses with validation in `__post_init__` prevent invalid tiers from reaching downstream. `DEFAULT_SOURCE_UPDATE_RULES` priority ordering is correct: TWSE (5) -> FinMind broker (10) -> FinMind datasets (15) -> Yahoo RSS (20) -> MOPS events (25) -> MOPS fundamentals (30) -> Yahoo fallback (90) -> FRED macro (80) -> FinMind bulk backfill (200). Archive handling in `build_active_universe_update_plan` produces correct `archive_detail_updates_skipped`. `_unique_symbols` deduplication is correct.

### main.py (source_ingestion)

Three new read-only endpoints:

- `GET /api/source-ingest/data-sources/financial-catalog`
- `GET /api/source-ingest/active-universe/policy`
- `POST /api/source-ingest/active-universe/plan`

Registry endpoint now embeds catalog and policy. No mutation of scheduler, watermarks, or connector lifecycle state. `active_universe_policy` endpoint derives policy from `financial_data_source_catalog_payload()` rather than calling `active_universe_policy_payload()` directly - minor inefficiency, not correctness issue.

### read_store.py (BFF)

BFF passthrough is correctly read-only. Extracts `financial_data_source_catalog` and `active_universe_policy` from service response with proper `isinstance` guards and `json.loads(json.dumps(...))` deep-copy pattern. Fallback for `active_universe_policy` nested inside catalog is correct. Summary fields (`financial_data_source_count`, `financial_data_source_template_count`, `active_universe_rule_count`) are correctly derived and exposed.

### Tests

Coverage is adequate:

- `test_financial_source_catalog.py`: catalog schema, providers, lifecycle state, template secret-ref invariant, active universe policy embedding
- `test_active_universe.py`: update plan symbol filtering by tier, rule overrides, policy tier definitions, transition record schema
- `test_service.py` additions: registry endpoint embeds catalog, dedicated catalog endpoint correctness
- `test_source_connector_service_client.py`: BFF service client reads catalog and policy fields
- `test_source_search_ops_bff.py`: ops snapshot includes catalog/policy summary fields

Pre-existing test failure noted by owner (`test_replay_dlq_requires_idempotency_key` detail shape mismatch) is not introduced by this task and is out of scope.

### Documentation

Design doc implementation notes are accurate and scoped. Correctly state that catalog entries are templates and do not claim live connector enablement.

## Verdict

**APPROVED.** All acceptance criteria met. Implementation is bounded to the declared scope. No regression risks in the added code. Recommend standard closeout.
