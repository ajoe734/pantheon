# P1-SOURCE-001 Acceptance Note

Owner: Codex
Reviewer: Claude
Task: News/social/alpha DB connector expansion

## Scope Delivered

- Added governed external-source policy for `news`, `social`, and `alpha_db`
  connector families in `services/source_ingestion/external_sources.py`.
- Connector registration/configuration now requires entitlement metadata for
  these source families and rejects direct Lean, broker, runtime, order-router,
  or execution targets.
- Configured fetch and inline ingest normalize external `SourceRecord` metadata
  for `license_scope`, `access_scope`, `entitlement_tags`, `event_time`,
  `available_time`, PIT validation, `content_hash`/`body_hash`, and a governance
  marker that keeps the canonical sink at `SourceRecord/EvidenceBundle`.
- `EvidenceBundle` now preserves bundle-level `available_time` and
  `entitlement_tags`; the bundle builder derives them from source/evidence
  metadata.
- Provider examples now include bounded governed examples for news, social, and
  alpha DB sources.

## Verification

```bash
python3 -m pytest services/source_ingestion -q
python3 -m pytest services/knowledge/evidence services/search/tests/test_governed_search.py services/search/test_index_pipeline.py services/search/tests/test_retrieval_rank_filter_cutoff_contract.py -q
```

Results:

- `52 passed` for `services/source_ingestion`
- `61 passed` for the focused evidence/search compatibility suite

## Boundary Notes

- News/social/alpha records fail before persistence/search if entitlement,
  available-time/PIT metadata, social trust metadata, or alpha signal schema
  metadata is missing.
- Alpha DB `allowed_use` rejects direct execution/live trading/order routing
  semantics.
- No new Lean, broker, runtime-manager, order router, or SignalStore feed path was
  added.
