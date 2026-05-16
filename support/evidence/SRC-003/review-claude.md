# SRC-003 Review — Claude

**Task**: repo allowlist ingest skeleton  
**Owner**: Codex  
**Reviewer**: Claude  
**Date**: 2026-05-16  
**Outcome**: APPROVED

## Scope Verified

- `services/source_ingestion/connectors/repo_allowlist.py` — `RepoAllowlistEntry`, `RepoAllowlistProvider`
- `services/source_ingestion/connectors/examples.py` — `example_provider_catalog` includes repo allowlist entry
- `services/source_ingestion/connectors/__init__.py` — exports `RepoAllowlistEntry`, `RepoAllowlistProvider`
- `services/source_ingestion/tests/test_repo_allowlist.py` — 3 targeted tests

## Governance Boundary — PASS

- `governance["direct_execution_allowed"] = False` ✓
- `governance["lean_consumption"] = "research_only_not_direct_action"` ✓
- `governance["broker_consumption"] = "not_direct_action"` ✓
- `governance["allowed_ingest_mode"] = "repo_allowlist_static_record"` ✓
- `license_policy.allowed_use = ("research", "search_index", "strategy_seed")` — no execution routes ✓
- `fetch_config()` emits `mode=static_records`, `next_watermark=None` — no live fetch ✓

## Input Validation — PASS

- `_REPO_FULL_NAME_RE` enforces `owner/repo` format; HTTP URLs rejected ✓
- Path traversal (`..`) detected and rejected ✓
- Absolute paths (leading `/` or `\`) rejected ✓
- Wildcards (`*`, `**` as standalone path components) rejected ✓
- Windows drive-letter syntax (`:` in path) rejected ✓
- Ref safety: `..`, leading `/`, and char-allowlist enforced via `_SAFE_REF_RE` ✓
- Duplicate `repo_full_name` within one provider detected and rejected ✓
- Empty allowlist rejected ✓

## Static Records Round-Trip — PASS

Provider correctly populates `SourceRecord` via `ConfiguredConnectorFetcher`; `content_ref` matches repo URL; `metadata` carries all required governance, allowlist, and citation fields.

## Provider Catalog — PASS

`example_provider_catalog()` includes `RepoAllowlistProvider(connector_id="example-github-repo-allowlist")` with `QuantConnect/Lean` as the example entry. Test validates lookup, `repo_allowlist_policy`, `allowlist_enforced`, and `repo_full_name`.

## Exports — PASS

`__init__.py` exports `RepoAllowlistEntry` and `RepoAllowlistProvider`; `SourceEvidenceError` and `example_provider_catalog` already exported.

## Test Results

```
python3 -m py_compile services/source_ingestion/connectors/repo_allowlist.py \
  services/source_ingestion/connectors/examples.py \
  services/source_ingestion/connectors/__init__.py
# no output (pass)

python3 -m pytest services/source_ingestion/tests/test_repo_allowlist.py -q
# 3 passed in 2.79s

python3 -m pytest services/source_ingestion/tests -q
# 46 passed in 72.64s
```

## Notes

- The wildcard guard only blocks standalone `*` / `**` path components. Patterns like `*.py` are not explicitly blocked, which is acceptable for an allowlist skeleton where operators control entries directly.
- `source_quality_score: 0.72` is a hardcoded skeleton default — appropriate for this phase.

## Decision

**APPROVED** — governance boundaries are correctly enforced, all validation guards are in place, static-records mode is correct, and all 46 source_ingestion tests pass.
