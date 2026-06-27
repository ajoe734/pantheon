# Task Brief: LOOP-AUTO-SRC-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add persona data requirement schema
- Status: review_approved
- Owner: Claude
- Reviewer: Codex
- Next: Finalization in progress. PR #2411 MERGEABLE, all CI pass. Verified 27 tests OK on closeout re-run. Awaiting PR merge then done.

## Summary
把 persona 的資料需求從 metadata label 升級成 first-class required_data_sources schema。

## Review
- Reviewed commit: 973ef5c900342a7e149663ddc8e84329710fe845.
- Verified: `python3 -m unittest discover -s services/control-plane/persona -p 'test_*.py'` (134 tests OK; 27 on closeout re-run OK).
- Verified: Draft7 check for `persona_registry.schema.json` and `required_data_sources.schema.json`; valid required_data_sources payload accepted, payload missing `source_class` rejected.
- Non-blocking follow-up: `docs/03/SD-02_persona_governance.md` has duplicate `### 4.7` headings; does not block schema acceptance.

## Closeout
- Owner: Claude
- PR: #2411 (open, MERGEABLE, awaiting merge into dev)
- Verified: `python3 services/control-plane/persona/test_persona_data_sources.py` — 27 tests OK
- CI: Commit trailers / Runtime mirror guard / Smoke acceptance — all pass
