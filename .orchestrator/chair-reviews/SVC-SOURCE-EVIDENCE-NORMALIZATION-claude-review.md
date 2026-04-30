# Task Review — SVC-SOURCE-EVIDENCE-NORMALIZATION

**Reviewer:** Claude
**Owner:** Codex2
**Branch:** backend-dev-publish-20260429
**Reviewed At:** 2026-04-30

---

## Acceptance Criteria Evaluation

| # | Criterion | Result |
|---|---|---|
| 1 | canonical evidence schema covers source record evidence item and knowledge object | PASS |
| 2 | content hash canonical url doi and repo normalization are deterministic | PASS |
| 3 | license access scope and citation metadata are preserved | PASS |
| 4 | dedupe merges duplicates without losing provenance | PASS |
| 5 | tests cover source search consultation compatibility | PASS |

---

## Findings

### AC-1: Canonical evidence schema
`normalization.py` defines `normalize_source_record`, `normalize_evidence_item`, and `normalize_source_evidence` covering the full ownership chain: `SourceRecord → EvidenceItem → KnowledgeObject`. `NormalizedEvidenceOwnership` is the deterministic bridge dataclass. `models.py` carries all three model types with `to_dict/from_dict` round-trip support. All fields required by the acceptance criterion are present.

### AC-2: Deterministic normalization
- `normalize_url`: lowercase scheme+host, strips default port, re-encodes path, sorts query params, strips trailing slash.
- `normalize_doi`: strips `doi:` prefix and `doi.org`/`dx.doi.org` host, lowercase, strips trailing punctuation.
- `normalize_repo_url`: delegates to normalize_url, strips `.git` suffix, limits path to `owner/repo`.
- `content_hash_for_record`: `sha256:` prefixed hex hash of `raw_content` or `content` or `body` field, or title+canonical_url fallback.
- Test `test_url_doi_and_repo_normalizers_produce_canonical_refs` verifies canonical form.
- Test `test_source_evidence_normalization_assigns_deterministic_evidence_owner` verifies that two calls with the same input produce the same `evidence_owner_id`.

### AC-3: License, access scope, citation metadata preserved
`normalize_source_record` records `license_scope` (from metadata, connector scope, or "internal" default) and normalizes `access_scope`. `normalize_evidence_item` propagates `canonical_url/doi/repo`, preserves `citation_label`, `access_scope`, `confidence`. `EvidenceBundle` and `KnowledgeObject` carry `license_scope` and `access_scope` as required fields.

### AC-4: Dedupe without provenance loss
`InMemoryEvidenceRepository.add_source_record` and `add_evidence_item` check the dedupe index before inserting; on collision they return the existing canonical owner without overwriting it. `source_owner_id` and `evidence_owner_id` are propagated into item metadata so downstream consumers can trace to the canonical owner. `JsonlEvidenceRepository` only appends when the stored record ID matches the incoming ID, preventing duplicate JSONL writes.

### AC-5: Test coverage
54 tests pass across:
- `services/knowledge/evidence/tests/test_normalization.py` — normalization unit tests
- `services/knowledge/evidence/tests/test_bundle.py` — bundle builder tests
- `services/source_ingestion/test_service.py` — FastAPI service integration tests
- `services/source_ingestion/test_postgres_store.py` — Postgres store tests
- `services/source_ingestion/tests/` — connector framework and scheduler tests
- `services/consultation/` — consultation service compatibility

---

## Review Decision

**APPROVED.** All five acceptance criteria are met. Implementation is narrow and traceable: new code is in `normalization.py` with clear ownership boundaries; modifications to `repository.py`, `main.py`, and `pg_store.py` are additive. Evidence note `svc-source-evidence-normalization.md` is complete. No canonical architecture docs were broadened.

**Next:** Codex2 to perform closeout finalization — task-scoped commit and `ai-status.sh done`.
