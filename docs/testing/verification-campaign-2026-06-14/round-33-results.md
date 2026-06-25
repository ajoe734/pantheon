# Round 33 — Results

**Executed:** 2026-06-15 (UTC).

## Audit

Exactly **one** non-BFF service has a naive `datetime.min` sort floor:
`services/search/retriever.py:55`.

## Finding

### F19 — search retriever 500s on a score tie with mixed-tz updated_at (FIXED)

`KeywordRetriever.retrieve` sorts matches by
`(item.score, item.updated_at or datetime.min)`. `updated_at` is
`document.indexed_at`, parsed by `_parse_time` → **aware** for `"...Z"`, naive
otherwise; `datetime.min` is naive. Tuples compare element-wise, so on a **score
tie** (scores are rounded to 3 decimals — ties are common) the datetimes are
compared, and an aware vs naive pair raises
`TypeError: can't compare offset-naive and offset-aware datetimes` (reproduced).

## Fix

Normalize the tie-breaker to naive:
`(item.updated_at or datetime.min).replace(tzinfo=None)` — same pattern as
F17/F18. Verified: a mixed-tz, tied-score set now sorts without raising; pre-fix
confirmed to `TypeError`.

Regression: `services/search/test_retriever_mixed_tz_sort.py` (1 passed) — three
documents with identical score and aware/naive/missing `indexed_at` retrieve
without raising.

## Net

F19 fixed — the last instance of the aware/naive sort-key bug in the fleet.
Combined with F17 (1) + F18 (20) + main.py's already-safe aware floors, the
**entire fleet's** timestamp sort keys are now tz-safe.
