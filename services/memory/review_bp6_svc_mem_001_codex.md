# BP6-SVC-MEM-001 Review

Reviewer: Codex
Date: 2026-04-17
Outcome: reopen -> changes requested

## Findings

1. Persisted JSON is not revalidated against the canonical schema during load.
   - `create()` runs both semantic validation and `validate_institutional_memory_json()` before accepting a new record ([institutional_memory_store.py](/home/edna/code/pantheon/services/memory/institutional_memory_store.py:207)).
   - `_load()` only reconstructs the dataclass and runs semantic validation, so schema-only violations such as extra `content` properties or unknown top-level fields are silently accepted ([institutional_memory_store.py](/home/edna/code/pantheon/services/memory/institutional_memory_store.py:335)).
   - Repro used during review: a persisted record with `content.extra` loads successfully even though `institutional_memory_entry.schema.json` sets `content.additionalProperties: false` ([institutional_memory_entry.schema.json](/home/edna/code/pantheon/services/memory/institutional_memory_entry.schema.json:22)).

2. Timestamp handling claims UTC semantics but accepts arbitrary timezone offsets, then sorts using the raw timestamp string.
   - `validate_institutional_memory()` accepts any timezone-aware timestamp because `_parse_utc_timestamp()` only rejects naive datetimes ([institutional_memory_store.py](/home/edna/code/pantheon/services/memory/institutional_memory_store.py:149)).
   - `list()` and retrieval tie-break ordering compare `entry.written_at` lexicographically instead of comparing normalized UTC instants ([institutional_memory_store.py](/home/edna/code/pantheon/services/memory/institutional_memory_store.py:261), [institutional_memory_store.py](/home/edna/code/pantheon/services/memory/institutional_memory_store.py:323)).
   - Repro used during review: `2026-04-17T08:00:00+01:00` is sorted ahead of `2026-04-17T07:30:00Z`, even though the first instant is earlier in UTC.

## Verification Run

- `python3 -m unittest services/memory/test_institutional_memory_store.py`
- `python3 services/memory/smoke_test_institutional_memory.py`

Both commands passed, but neither covers the two defects above.
