# Task Brief: MPOS-P1-MEM-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add first class PersonaMemory retrieval and writeback
- Status: done (closeout prepared)
- Owner: Codex
- Reviewer: Claude
- Next: Closeout prepared after PR #1215 merged into dev; focused validation rerun passed.

## Summary
在 institutional memory 之外補齊 per persona memory entry、授權 retrieval、writeback triggers，讓 persona 真的有自己的記憶而不是只共享機構知識。

## Closeout Verification (2026-06-09)
Review approval: Claude approved the task after confirming all 5 acceptance criteria, 28/28 memory tests, and fail-closed authz behavior.

Commands rerun by owner:
- `python3 -m unittest services/memory/test_persona_memory_store.py services/memory/test_main.py` - 28 tests passed.
- `python3 -m pytest services/governance/test_governance_api.py services/foundation/tests/test_control_plane_postgres_owner_stores.py -q` - 32 tests passed.
- `python3 services/memory/smoke_test_institutional_memory.py` - 22 smoke checks passed, including persona writeback/retrieval replay.

## Finalization Record
Implementation PR #1215 merged into `dev` at `1d29b0012732fc1c0059a30c566e61b551913a8f` on 2026-06-09. GitHub branch gate checks passed: Commit trailers, Runtime mirror guard, and Smoke acceptance.
