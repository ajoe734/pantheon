# LSP-004-V2 Review — Forbidden Runtime Path Scanner

**Reviewer:** Claude  
**Date:** 2026-05-19  
**Status:** APPROVED

## Artifacts Reviewed

- `scripts/lovable/forbidden_path_scanner.py`
- `tests/lovable/test_forbidden_path_scanner.py`

## Acceptance Criteria Check

| Criterion | Status | Notes |
|-----------|--------|-------|
| Scans hosted JS bundles for forbidden signals | ✅ | `scan_deployment()` fetches HTML, discovers bundle URLs via `<script src>`, `<link rel="modulepreload">`, and inline Vite asset refs, then scans each |
| Detects `/mocks/` | ✅ | Pattern `/mocks/` with `re.IGNORECASE` |
| Detects `seed.` | ✅ | `\bseed\.` with word boundary to avoid false positives |
| Detects `mockSeed` | ✅ | `\bmockSeed\b` with word boundaries |
| Detects silent fallback markers | ✅ | `(?:silent[\s_-]?fallback\|fallback[\s_-]?silent)` with negative lookbehind |
| Detects `VITE_BFF_FALLBACK=auto` | ✅ | Handles both `=` and `:` separators, with or without quotes |
| Detects local seed hydration | ✅ | Matches camelCase and kebab/snake variants |
| Fails closed on fetch errors | ✅ | `passed = not errors and not forbidden_signals` |
| Returns `ScanResult` dict | ✅ | TypedDict with all required fields |
| Exit code 1 on failure | ✅ | `main()` returns `0 if result["passed"] else 1` |

## Test Verification

```
$ python3 -m pytest tests/lovable/test_forbidden_path_scanner.py -v
collected 4 items
tests/lovable/test_forbidden_path_scanner.py::test_scan_deployment_passes_when_hosted_bundles_are_clean PASSED
tests/lovable/test_forbidden_path_scanner.py::test_scan_deployment_fails_on_each_forbidden_signal PASSED
tests/lovable/test_forbidden_path_scanner.py::test_scan_deployment_fails_when_asset_fetch_fails PASSED
tests/lovable/test_forbidden_path_scanner.py::test_scan_deployment_scans_direct_js_url PASSED
4 passed in 0.41s
```

## Code Quality Notes

- Standard library only — no external dependencies.
- `_AssetParser` cleanly separates HTML parsing from scanning logic.
- `_line_column` and `_snippet` provide operator-friendly error reports.
- Deduplication via `seen` set prevents duplicate signals.
- Deterministic output (sorted signals, sorted URLs).
- Module is importable and has a functional CLI entry point.

## Verdict

Implementation is complete and correct. All 6 forbidden signal patterns are covered, fail-closed semantics are enforced, and all 4 tests pass cleanly.
