# Round 8 — Results

**Executed:** 2026-06-14 (UTC).

## PUT/PATCH/DELETE robustness (H1: PASS)

17 routes probed with an unknown id (malformed body for PUT/PATCH):
`422×15, 400×1, 404×1`, **zero 5xx**. The mutating-method surface degrades
cleanly — no unhandled exceptions.

## Archive integrity (H2/H3)

`ai-task-archive/`: 1,481 task files, all distinct ids, no duplicates, no
unparseable JSON. `terminal_status` tally: `done×1480`, `null×1`. `index.json`
`counts`: `total=1474` (`completed=1450`, `superseded=24`).

Two observations:

- **Index count lag (O3, not fixed):** `total=1474` vs 1,481 files — the index
  is a committed snapshot that lags by the files archived since the last
  `rebuild_archive_index`. This self-heals on the orchestrator's next archive
  operation and is runtime-owned state; not edited here (a hand-edit would be
  overwritten by the live orchestrator).

- **F8 (FIXED) — indexer silently drops legacy-schema archive entries.**
  `rebuild_archive_index` (`.orchestrator/task_archive.py`) resolved the task id
  as `snapshot.get("task_id") or snapshot["task"]["id"]` and **skipped** any
  file resolving to None. `ai-task-archive/tasks/OSS-STAT-001-SIDECAR-ACCEPTANCE.json`
  stores its id at the top level as `id` (legacy schema), so it resolved to None
  and was **excluded from the index permanently** — invisible to every consumer
  of the archive index (dashboard terminal counts, recent-terminal lists), and
  un-counted no matter how often the index is rebuilt.

## Fix (F8 — dev workflow)

Extend the id resolution to fall back to the top-level `id`:
`snapshot.get("task_id") or snapshot["task"]["id"] or snapshot.get("id")`.
The change is **strictly additive** — files that already resolved an id are
unaffected; only files that previously resolved to None (and have a top-level
`id`) are now indexed. Regression test
`.orchestrator/test_task_archive_index_legacy_id.py` asserts a legacy
top-level-`id` snapshot is indexed (`total==2`, both ids in
`recent_terminal_ids`). Existing archive tests in `scripts/test_ai_status.py`
remain green (5 passed).

## Net

H1 **PASS** (no 5xx on PUT/PATCH/DELETE). H3 had one real defect (F8) — the
archive indexer silently lost legacy-schema tasks — now fixed and locked by a
regression test. O3 (index count lag) is runtime-owned and self-healing,
recorded not fixed.
