# Round 8 — Write-method robustness + orchestrator archive integrity

**Date:** 2026-06-14
**Depth/breadth step:** Closes the last untested HTTP-method surface
(PUT/PATCH/DELETE), then broadens into a **second plane** — the orchestrator
task-archive state and the tooling that indexes it — checking structural
invariants no prior doc verifies.

## Why this round (not a duplicate)

Rounds 2–4 swept GET and POST; PUT/PATCH/DELETE were never exercised for 500s.
And no doc verifies the `ai-task-archive` ↔ `index.json` consistency or the
`rebuild_archive_index` id-resolution logic.

## Hypotheses

- H1: every PUT/PATCH/DELETE route returns a clean 4xx (404/422/400) for an
  unknown id — never 500.
- H2: the task archive is internally consistent — index counts reconcile with
  the task files; every archived task is representable in the index.
- H3: `rebuild_archive_index` indexes every archived task file regardless of
  which schema variant stores the id.

## Method

1. Enumerate PUT/PATCH/DELETE routes (17); probe each with an unknown id (and
   malformed body for PUT/PATCH — mutation-safe).
2. Tally the 1,481 archive task files by `terminal_status`; compare to
   `index.json` counts; check for duplicate ids and filename/id mismatch.
3. Read `rebuild_archive_index`; identify any task file silently excluded.

## Pass criteria

- H1: zero 5xx on the PUT/PATCH/DELETE surface.
- H2/H3: every archive file is indexable; any silently-dropped file is a defect,
  fixed via the dev workflow.
