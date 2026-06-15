# Round 31 — Results

**Executed:** 2026-06-15 (UTC).

## Audit

9 `utcnow()` usages in non-test service code. 8 are immediate serialization
(`.isoformat() + "Z"` or `.timestamp()`) — safe (no naive/aware comparison). One
(`read_store.py:13445`, `reference_now = datetime.utcnow()`) is later used in a
subtraction, but the code correctly does `parsed.replace(tzinfo=None)` first —
naive − naive, safe.

But the audit of that method surfaced a separate, real aware/naive bug nearby.

## Finding

### F17 — `list_research_analyses` 500s on mixed-tz `run_at` (FIXED)

`ReadSurfaceStore.list_research_analyses` (`read_store.py`) sorts:

```python
analyses.sort(key=lambda a: _parse_rfc3339(a.get("run_at")) or datetime.min, reverse=True)
```

`_parse_rfc3339` returns a **timezone-aware** datetime for a tz-bearing `run_at`
(e.g. `"...Z"`), but the `datetime.min` fallback (when `run_at` is missing/
unparseable) and a tz-less `run_at` are **naive**. Sorting a list whose keys mix
aware and naive datetimes raises:

> `TypeError: can't compare offset-naive and offset-aware datetimes`

→ a 500 on the research-analyses list endpoint whenever the dataset contains
**both** a tz-aware and a naive/missing `run_at` (reproduced).

## Fix

Normalize the sort key to naive — `(_parse_rfc3339(run_at) or datetime.min)
.replace(tzinfo=None)` — consistent with the date-range cutoff comparison in the
same method (which already strips `tzinfo`). Verified: a mixed-tz set now sorts
newest-first without raising.

Regression: `test_research_analyses_mixed_tz_sort.py` (1 passed) seeds four
records (aware `Z`, missing → `datetime.min`, naive, aware `+00:00`) and asserts
no `TypeError` + correct ordering. Confirmed the pre-fix key raises `TypeError`
on the same data.

## Net

F17 fixed — a latent aware/naive `TypeError` 500 in the research-analyses list
sort, found via the `utcnow`/datetime-mixing audit and locked by a regression
test. (The other 8 `utcnow()` usages are serialization-only and safe.)
