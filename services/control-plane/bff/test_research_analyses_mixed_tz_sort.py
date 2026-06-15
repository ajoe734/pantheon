"""Regression: list_research_analyses must not raise when run_at values mix
timezone-aware and naive datetimes.

Verification campaign 2026-06-14, round 31, finding F17. The sort key
`_parse_rfc3339(run_at) or datetime.min` produced an aware datetime for
"...Z" run_at but a naive datetime.min fallback (and for tz-less run_at);
sorting a mix of aware + naive datetimes raises
"TypeError: can't compare offset-naive and offset-aware datetimes" -> 500.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

BFF_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BFF_DIR))

from read_store import ReadSurfaceStore  # noqa: E402


def _store():
    fd = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({"surfaces": {}}, fd)
    fd.close()
    return ReadSurfaceStore(fd.name, allow_local_snapshot_fallback=False)


MIXED = [
    {"id": "a-aware", "run_at": "2026-01-03T00:00:00Z"},      # aware
    {"id": "b-missing", "run_at": None},                       # -> datetime.min (naive)
    {"id": "c-naive", "run_at": "2026-01-02T00:00:00"},        # naive
    {"id": "d-aware2", "run_at": "2026-01-01T00:00:00+00:00"}, # aware
]


def test_list_research_analyses_mixed_tz_does_not_raise(monkeypatch):
    store = _store()
    monkeypatch.setattr(
        store, "_read_dataset_records", lambda *a, **k: [dict(r) for r in MIXED]
    )
    # Must not raise TypeError on the aware/naive sort.
    result = store.list_research_analyses()
    assert isinstance(result, list)
    assert len(result) == 4
    # Newest first: the aware 2026-01-03 record leads despite the mixed-tz set.
    assert str(result[0].get("run_at")).startswith("2026-01-03")
