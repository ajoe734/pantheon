# LSP-003-V2 Review — Claude

**Reviewer:** Claude
**Date:** 2026-05-19
**Status:** APPROVED

## Verification

```
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/lovable/capture_bundle_hashes.py tests/lovable/test_capture_bundle_hashes.py
# → OK

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/lovable/test_capture_bundle_hashes.py
# → 3 passed in 0.38s

git diff --check origin/dev...HEAD -- scripts/lovable/capture_bundle_hashes.py tests/lovable/test_capture_bundle_hashes.py
# → OK (no whitespace errors)
```

## Code Review

### `scripts/lovable/capture_bundle_hashes.py`

- TypedDicts (`AssetHash`, `BundleCaptureResult`) provide a clear, typed contract.
- Dependency-injected `http_get: HttpGet | None` enables deterministic unit testing without network calls.
- `_HtmlAssetParser` correctly handles both `<script src>` and `<link rel="modulepreload">` / `<link rel="preload" as="script">` patterns.
- Inline Vite bootstrap asset regex (`/assets/...\.m?js`) catches chunk references embedded in the HTML bootstrap code.
- Recursive JS chunk discovery uses BFS with a `seen` set and `max_js_assets` bound — correctly prevents infinite loops and runaway fetches.
- `write_audit_packet` merges without overwriting existing keys — correct for composing with an upstream audit packet.
- No live broker, no capital side effects.

Minor notes (non-blocking):
- `# pragma: no cover` on line 172 is inaccurate; `test_capture_bundle_hashes_records_js_fetch_failure` does exercise that path via a `KeyError` from the fake fetcher. The annotation won't affect test correctness but is misleading in coverage reports.
- `queue.pop(0)` is O(n); `collections.deque` would be idiomatic for BFS. At `max_js_assets=200` this is immaterial.

### `tests/lovable/test_capture_bundle_hashes.py`

- Three tests cover: happy path (index + 3 JS chunks with recursive discovery), audit packet merge, and JS fetch failure.
- All tests are fully deterministic (injected fetcher, fixed `checked_at`).
- SHA-256 assertions compare against `hashlib.sha256(...).hexdigest()` recomputed in-test — not hardcoded strings.
- Recursive discovery is verified end-to-end (chunk-333.js surfaced via import in index-111.js).

## Acceptance

Task scope (bundle hash recorder for hosted Lovable index + JS chunks) is fully implemented and tested.
Artifacts: `scripts/lovable/capture_bundle_hashes.py`, `tests/lovable/test_capture_bundle_hashes.py`.
PR #213 merged into dev at 2026-05-19T15:24:17Z (merge commit 3f8fab29e86af7ff38c2cb629dc64fdce84ac9f1).

**APPROVED — returning to Codex2 for closeout.**
