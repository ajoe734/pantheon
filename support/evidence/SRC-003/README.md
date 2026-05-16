# SRC-003 Closeout Evidence

Task: repo allowlist ingest skeleton
Owner: Codex
Reviewer: Claude
Status: review_approved -> done closeout
Date: 2026-05-16

## Delivered Scope

- Added `RepoAllowlistEntry` and `RepoAllowlistProvider` for governed GitHub repository allowlist ingestion.
- Kept repo ingestion bounded to `static_records` fetch config with `next_watermark=None`.
- Rejected arbitrary URLs, path traversal, absolute paths, wildcard path components, unsafe refs, empty allowlists, and duplicate repositories.
- Marked repository records as research-only with no direct execution or broker route.
- Exposed `example-github-repo-allowlist` in the source connector example catalog.
- Added targeted tests for static-record output, validation guards, and catalog exposure.

## Reviewer Approval

Claude approved the task in `support/evidence/SRC-003/review-claude.md`.

## Closeout Verification

```bash
python3 -m py_compile services/source_ingestion/connectors/repo_allowlist.py services/source_ingestion/connectors/examples.py services/source_ingestion/connectors/__init__.py
python3 -m pytest services/source_ingestion/tests/test_repo_allowlist.py -q
python3 -m pytest services/source_ingestion/tests -q
```

Results:

- `py_compile`: passed
- `test_repo_allowlist.py`: 3 passed in 3.62s
- `services/source_ingestion/tests`: 46 passed in 76.69s
