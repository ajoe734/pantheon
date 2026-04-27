# SD-CONSULT-001 Codex Handoff

Status: ready for Codex2 review
Owner: Codex
Reviewer: Codex2
Date: 2026-04-27

## Scope Completed

- Repaired `services.consultation` package imports and runnable smoke entrypoints.
- Added service-owned request lifecycle records, committee participant assignment,
  debate transcript events, append-only evidence attachments, published memo
  immutability, gate handoff records, and audit events for lifecycle transitions.
- Updated the consultation Docker entrypoint to import the service as
  `services.consultation.main:app`.

## Review Notes

The earlier SD-CONSULT-001 review findings are addressed:

1. Repo-root smoke tests now run through package imports.
2. Governance gate handoff now carries memo refs, evidence refs, trace id, and
   audit refs.
3. Published memo records are immutable at the store boundary; first publication
   is also appended to `consult_memo_publications.jsonl`.
4. Request submit, participant assignment, evidence attach, transcript append,
   memo submit, memo publish, and gate handoff creation emit audit events.

## Verification

- `python3 -m unittest services.consultation.smoke_test`
- `python3 -m services.consultation.smoke_test`
- `python3 services/consultation/run_smoke.py`
- `python3 services/consultation/run_smoke_logic.py`
- `python3 -m py_compile services/consultation/main.py services/consultation/models.py services/consultation/store.py services/consultation/smoke_test.py services/consultation/run_smoke.py services/consultation/run_smoke_logic.py`
- `python3 -c 'from services.consultation.main import app; print(app.title)'`
