# Task Brief: LOOP-AUTO-SRC-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add persona data requirement schema
- Status: review_approved
- Owner: Claude
- Reviewer: Codex
- Next: Review approved: RequiredDataSource and Persona.required_data_sources satisfy all three acceptance criteria; 134 persona tests pass; Draft7 schema validation covers valid and missing-source_class payloads. PR #2411 remains BEHIND latest dev and must be refreshed before merge/finalization.

## Summary
把 persona 的資料需求從 metadata label 升級成 first-class required_data_sources schema。

## Review
- Reviewed commit: 973ef5c900342a7e149663ddc8e84329710fe845.
- Verified: `python3 -m unittest discover -s services/control-plane/persona -p 'test_*.py'` (134 tests OK).
- Verified: Draft7 check for `persona_registry.schema.json` and `required_data_sources.schema.json`; valid required_data_sources payload accepted, payload missing `source_class` rejected.
- Non-blocking follow-up: `docs/03/SD-02_persona_governance.md` now has duplicate `### 4.7` headings; this does not block the schema acceptance but should be cleaned during doc polish.
