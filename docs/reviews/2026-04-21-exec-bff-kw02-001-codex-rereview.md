# EXEC-BFF-KW02-001 Review

Reviewer: `Codex`
Date: `2026-04-21`
Disposition: `changes_requested`

## Findings

1. `GET /api/v1/knowledge/notes` still derives `meta.surfaces.research_note_list` from row count instead of dataset availability, so a valid but empty backend-owned store is reported as `unavailable`.

- The latest fix changed the surface-health check from filtered rows to pre-filter rows, but it still uses `dataset_has_notes = bool(notes)` at [services/control-plane/bff/main.py](/home/edna/code/pantheon/services/control-plane/bff/main.py:7219) and passes that into `_kw02_surface_state(...)` at [main.py](/home/edna/code/pantheon/services/control-plane/bff/main.py:7246).
- This is still contract-wrong for an empty yet available dataset. The store layer already exposes availability separately from row count through `dataset_source()` returning `service_store` when the backend-owned JSON store exists, even if it is empty, at [read_store.py](/home/edna/code/pantheon/services/control-plane/bff/read_store.py:3744).
- Reproduction: using the existing `_service_backed_client()` harness with an empty `research_notes.json`, `GET /api/v1/knowledge/notes` returns `200`, `notes=[]`, and `meta.surfaces.research_note_list='unavailable'`.
- Result: the BFF tells the frontend to show the canonical unavailable/degraded path for a healthy but empty list surface, which violates the KW-02 design rule that surface health must reflect backend availability rather than empty arrays.
- Required fix: derive list surface health from dataset availability/source, not `bool(notes)`, and add a regression that covers the truly empty service-backed store before any note is created.

## Verification

- `pytest -q services/control-plane/bff/test_kw02_research_notes_contract.py services/control-plane/bff/test_kw01_institutional_memory_contract.py services/control-plane/bff/test_pkt016_knowledge_workbench_contract.py` passed (`9 passed`).
- Reproduced the bug with the existing KW-02 service-backed test harness and confirmed the response is `200`, `notes=[]`, `meta.surfaces={'research_note_list': 'unavailable'}` when the backend-owned `research_notes` store is present but empty.
