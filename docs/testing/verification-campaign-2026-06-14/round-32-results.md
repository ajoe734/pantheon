# Round 32 — Results

**Executed:** 2026-06-15 (UTC).

## Finding

### F18 — 20 more `or datetime.min` sort keys had the F17 bug (FIXED)

F17 (Round 31) was **not** isolated. `read_store.py` had **21**
`_parse_rfc3339(...) or datetime.min` sort keys (F17 fixed 1); the other **20**
share the identical aware/naive `TypeError` bug — each list endpoint that sorts
by a timestamp (interventions, research tickets/notes, prompt versions, audit
entries, experiments, artifacts, runs, committee memos, lineage entries, …)
would 500 whenever its dataset contains both a tz-aware (`...Z`) and a
naive/missing timestamp.

main.py, by contrast, already uses an **aware** floor
(`datetime.min.replace(tzinfo=timezone.utc)`) at its 7 sort sites — so the bug
was confined to `read_store.py`.

## Why not a one-line global fix

Making `read_store._parse_rfc3339` always-naive would fix the sort keys in one
edit but **break comparison sites**: `list_governance_audit_events` compares
`_parse_rfc3339(event.timestamp)` against `from_ts`, which is fed by main.py's
**aware** `_parse_rfc3339` (the F12 audit filter). Forcing the event side naive
would re-introduce an aware/naive `TypeError` there. So the fix is applied to the
**sort keys only**, leaving comparison operands untouched.

## Fix

Wrapped each of the 20 sort-key expressions with `.replace(tzinfo=None)`
(`(... or datetime.min).replace(tzinfo=None)`), matching the F17 fix — the whole
key becomes naive, so no sort can mix awareness. Comparison sites unchanged
(F12's audit filter intact). 27 lines changed, all tz-wraps.

## Verification

- `test_read_store_sort_key_tz_safe.py` (static guard, 1 passed): asserts every
  `or datetime.min` floor in `read_store.py` is tz-normalized — a future
  un-normalized sort key fails CI.
- `test_research_analyses_mixed_tz_sort.py` (F17, still passing).
- `test_ask_003_committee_lifecycle.py` + journal tests (read_store integration):
  green. **31 passed** total.

## Net

F18 fixed — 20 sibling latent `TypeError` 500s across the read-store sort
surface, generalizing F17, with a static guard preventing regressions. The
deliberately-narrow scope leaves the F12 audit-filter comparison untouched.
