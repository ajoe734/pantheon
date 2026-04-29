# Review: SVC-SOURCE-INGEST-EXTERNAL-FETCH-BASELINE

Reviewer: Codex
Date: 2026-04-29

## Scope Verified

- `services/source_ingestion/configured.py` - configured fetch modes, URL allowlist, bounds, metadata defaults, fetch attempt state.
- `services/source_ingestion/main.py` - HTTP request models, connector configuration, job trigger path, DLQ replay path.
- `services/source_ingestion/test_service.py` - static-record compatibility, external HTTP/file feed coverage, DLQ/watermark behavior, search access-scope propagation.
- `services/source_ingestion/tests/test_ingest_run.py` - scheduler watermark and DLQ/audit behavior.
- `scripts/smoke_honest_stack.py` - default-stack source-ingest configured fetch and DLQ replay smoke path.
- `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/phase2-phase6-gap-inventory.md` - current bounded-feed truth and non-crawler boundary.

## Acceptance Criteria Result

| Criterion | Result |
|---|---|
| static_records remains supported | PASS |
| external feed mode enforces allowlist, timeout, byte cap, and record cap | PASS |
| fetched records preserve license and access scope into evidence refs | PASS |
| failures route to DLQ/replay and do not advance watermark | PASS |
| focused source-ingestion tests and compose config pass | PASS |

## Implementation Notes

- `static_records` behavior remains on the configured connector path and existing configured fetch tests still pass.
- `external_feed` supports `http`, `https`, and `file` URLs only, requires `allowed_url_prefixes`, validates timeout/max bytes/max records, and re-checks HTTP redirect targets.
- External feed records inherit connector `license_scope`, configured default `access_scope`, and feed source metadata unless a record provides narrower metadata.
- Oversized external feed failures are converted into scheduled-run DLQ entries; the watermark remains absent/unchanged on failed fetch.
- The smoke stack now serves a local allowlisted HTTP JSON feed and triggers source-ingest by `connector_id` alone, while keeping configured DLQ replay coverage.

## Verification

```bash
python3 -m pytest services/source_ingestion/test_service.py services/source_ingestion/tests/test_ingest_run.py
# 17 passed in 3.74s

python3 -m pytest services/source_ingestion/test_compose_activation.py
# 1 passed in 0.39s

python3 -m py_compile services/source_ingestion/configured.py services/source_ingestion/main.py services/source_ingestion/test_service.py scripts/smoke_honest_stack.py
# exit 0

git diff --check -- services/source_ingestion/configured.py services/source_ingestion/main.py services/source_ingestion/test_service.py scripts/smoke_honest_stack.py docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/phase2-phase6-gap-inventory.md
# exit 0

docker compose config --quiet
# exit 0
```

## Decision

Approved. The implementation satisfies the bounded external fetch baseline without widening the scope into arbitrary crawling or live web scraping. Returning to Codex2 for task-scoped closeout.
