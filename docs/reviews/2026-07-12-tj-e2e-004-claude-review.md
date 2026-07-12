# TJ-E2E-004 Claude Review

Reviewer: Claude
Owner: Codex
Date: 2026-07-12
Disposition: approved (round 2)

## Round 2 Update (2026-07-12)

Reviewed commit `edee66dd8` ("fix mixed-precision event ordering") against
the round-1 blocking finding below.

Root cause confirmed by reading `_normalize()` prior to the fix: it called
`parsed.astimezone(timezone.utc).isoformat()` with no `timespec`, then did
`.replace("+00:00", "Z")`. Python's default `isoformat()` omits the
fractional-second component entirely when `microsecond == 0`, so a
whole-second event normalized to `...T00:00:00Z` (ending directly in the
literal `Z`, `0x5A`), while a same-second event with a nonzero microsecond
normalized to `...T00:00:00.500000Z` (containing `.`, `0x2E`, before the
digits). Because `_sort_key()` and `source_watermarks` both compare
`occurred_at` as a plain string, and `Z` (0x5A) sorts after both `.` (0x2E)
and any digit (0x30-0x39), a whole-second timestamp would lexicographically
sort *after* a fractional-second timestamp from the same second even when it
occurred at or before it — corrupting timeline order, `snapshot.created_at`/
`updated_at`, and `source_watermarks` for any producer that mixes
whole-second and fractional-second `occurred_at` precision.

The fix adds `timespec="microseconds"` to the `isoformat()` call, so every
normalized `occurred_at` always carries the full six-digit fraction
(`...T00:00:00.000000Z` for the whole-second case). Since all timestamps are
converted to UTC first (fixed `+00:00`/`Z` suffix, no offset variance) and
now share a fixed width, plain string comparison is monotonic with actual
time again.

New regression test `test_mixed_timestamp_precision_is_ordered_chronologically`
(`services/trade_journey/test_materializer.py:35-48`) reproduces the exact
scenario — one whole-second event, one `.500000`-second event in the same
second, ingested out of order via `rebuild()` — and asserts timeline order,
`snapshot.created_at`/`updated_at`, and `source_watermarks` are all correct.
I manually re-derived the pre-fix lexicographic comparison above and traced
it against the new test's inputs to confirm it would have failed before the
fix and passes after.

Verification (re-run independently, not just trusting the trailer):

```
python3 -m pytest -q services/trade_journey/test_materializer.py
# 8 passed

python3 -m py_compile services/trade_journey/materializer.py
git diff --check
# clean
```

Diff scope for this commit is exactly `services/trade_journey/materializer.py`
(4 lines) and `services/trade_journey/test_materializer.py` (16 lines) — no
other files touched, no producer contract or BFF surface changed, consistent
with the commit's own "Not changing" note.

## Verdict: Approved

Round 1's blocking finding (mixed whole/fractional-second `occurred_at`
precision breaking lexicographic ordering) is resolved with a minimal,
correctly-targeted fix and a regression test that would have caught it.
Handing back to owner (Codex) for closeout.

## Round 1 Finding (recorded here for the record; originally communicated via task-status `next` field, no round-1 file was written)

`JourneyMaterializer._normalize()` used `datetime.isoformat()` without a
`timespec`, so events with `microsecond == 0` produced an `occurred_at`
string with no fractional component while events with a nonzero microsecond
produced one with a six-digit fraction. Because ordering (`_sort_key`) and
watermark tracking (`source_watermarks`) both use plain lexicographic string
comparison on `occurred_at`, and the ASCII value of the literal `Z` sorts
after `.` and all digits, whole-second events could sort after
same-second fractional events regardless of actual chronological order —
violating the "deterministic ordering: occurred_at, sequence, event_id"
guarantee called out in the class docstring and required by the task's
"Handle duplicate, out-of-order, late and correction events idempotently"
acceptance criterion whenever real producers mix timestamp precision.

## Scope Reviewed

- `services/trade_journey/materializer.py`
- `services/trade_journey/test_materializer.py`
- `docs/bff/execution-tasks/2026-07-11-trade-journey-e2e/TJ-E2E-004-materializer-reverse-index.md`
- commits `fa605aeca` (anchor), `b0bfbbda8` (acceptance evidence), `edee66dd8` (fix)
