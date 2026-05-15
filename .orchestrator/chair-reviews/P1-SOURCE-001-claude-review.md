# Review: P1-SOURCE-001 — News/social/alpha DB connector expansion

Reviewer: Claude
Date: 2026-05-01

## Scope Verified

- `services/source_ingestion/external_sources.py` — connector-level and record-level policy validator for news/social/alpha_db source types; PIT, entitlement, ACL, license, content_hash enforcement; forbidden allowed_use and route rejection.
- `services/source_ingestion/configured.py` — applies `validate_external_source_connector` at configuration time and `validate_external_source_record` at fetch time.
- `services/source_ingestion/connectors/examples.py` — added example connectors for news, social, and alpha_db with entitlement_tags, access_scope, and source-family metadata.
- `services/knowledge/evidence/models.py` — `EvidenceBundle` now carries `available_time` and `entitlement_tags` as first-class fields.
- `services/knowledge/evidence/bundle_builder.py` — `build_bundle()` collects `available_time` and `entitlement_tags` from source records and evidence items and propagates them to the bundle.
- `services/source_ingestion/tests/test_external_source_connectors.py` — focused tests for all three source families.
- `services/source_ingestion/test_service.py` — end-to-end API test for news connector preserving entitlement and PIT on EvidenceBundle.
- `docs/04/pantheon_sa/SA-16_data_search_external_source_gap_analysis.md` — Section 13.1 documents the implementation.
- `docs/04/pantheon_sa/SA-20_v2_risk_register_corrected.md` — R-DATA-004 documents P1-SOURCE-001 as mitigating the news/social/alpha bypass risk.

## Acceptance Criteria Result

| Criterion | Result |
|---|---|
| news/social/alpha connector emits SourceRecord/EvidenceBundle | PASS |
| license/entitlement/available_time and PIT semantics enforced | PASS |
| no connector can feed Lean or broker directly | PASS |

## Implementation Notes

- `validate_external_source_connector()` runs at `upsert_config()` time: enforces `entitlement_tags` or `entitlement_ref`, rejects `allowed_use` containing execution/broker/lean tokens, and rejects routing metadata targeting lean/broker/runtime/execution paths.
- `validate_external_source_record()` runs at fetch time: requires `event_time`, `available_time`, and `available_time >= event_time` (PIT invariant); embeds `governance.direct_execution_allowed: false` and `governance.canonical_sink: SourceRecord/EvidenceBundle` into every governed record.
- Source-family validators enforce type-specific required fields: news (`publisher`, `published_at`, `source_uri`), social (`platform`, `author_id_hash`, `post_id`, `platform_policy_ref`, `trust_score` 0–1), alpha_db (`alpha_vendor_id`, `signal_id`, `signal_version`, `field_schema`, `universe`, `as_of_time`).
- `EvidenceBundle.available_time` is set to the latest observed `available_time` from source records and evidence items, preserving PIT semantics at the bundle level.
- `entitlement_tags` are union-collected from connector metadata, source record metadata, and evidence item metadata.
- SA-16 Section 13.1 and SA-20 R-DATA-004 are updated to reflect the bounded implementation scope; production vendor credential activation remains deferred as a separate rollout.

## Verification

```bash
python3 -m pytest services/source_ingestion -q
# 52 passed

python3 -m pytest services/knowledge/evidence services/search/tests/test_governed_search.py services/search/test_index_pipeline.py services/search/tests/test_retrieval_rank_filter_cutoff_contract.py -q
# 61 passed
```

## Decision

Approved. The implementation satisfies all three acceptance criteria: news/social/alpha_db connectors emit governed SourceRecord/EvidenceBundle; PIT, entitlement, license, and ACL fields are enforced at both connector and record layers; direct Lean/broker/runtime targets are rejected by both allowed_use policy and route-key inspection. Returning to Codex (owner) for task-scoped closeout.
