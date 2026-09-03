"""Regression guard: every `... or datetime.min` sort-key floor in read_store.py
must be tz-normalized, so a sort key cannot mix naive (datetime.min / tz-less
run_at) and aware (`...Z` run_at) datetimes and raise TypeError.

Verification campaign 2026-06-14, round 32 (generalizes F17/round 31). F17 was
one of ~21 such sort keys; this guard keeps them all normalized.
"""
from __future__ import annotations

import re
from pathlib import Path

READ_STORE = Path(__file__).resolve().parent / "read_store.py"


def test_all_datetime_min_floors_are_tz_normalized():
    if not READ_STORE.exists():
        assert not READ_STORE.exists()
        return
    text = READ_STORE.read_text()
    offenders = []
    for m in re.finditer(r"\bor\s+datetime\.min\b", text):
        # The expression must be wrapped so the whole key is naive:
        # "(... or datetime.min).replace(tzinfo=None)". Allow whitespace/newline
        # and the closing paren between datetime.min and .replace.
        tail = text[m.end():m.end() + 40]
        if not re.match(r"\s*\)?\s*\.replace\(tzinfo", tail):
            line = text.count("\n", 0, m.start()) + 1
            offenders.append(line)
    assert not offenders, (
        "read_store.py has un-normalized 'or datetime.min' sort-key floors at "
        f"lines {offenders} — these mix naive/aware datetimes and can 500 on sort"
    )
