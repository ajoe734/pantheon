# Review: BP5-SVC-014 — Realize persona platform and consultation read surfaces

**Reviewer:** Codex  
**Task:** BP5-SVC-014  
**Date:** 2026-04-16  
**Decision:** APPROVED

## Result

No remaining blocking findings.

The follow-up patch in commit `3f7e6fd` closes the three issues from the prior review round:

1. CS-03/04/05 now resolve responder session ids back to the authoritative requester/root consultation via `metadata.consultation.root_session_id`.
2. The seed set now includes both `p-risk-analyst` and `cp-risk-analyst`, so consultation policy refs and participant persona links resolve to canonical seeded objects.
3. The consultation route tests now include `TestClient` coverage for requester flow, responder-via-links flow, and participant persona link resolution.

## Verification

- `pytest -q services/control-plane/bff/test_consultation_surfaces.py`
  - Result: `4 passed`
- `pytest -q services/control-plane/bff/test_persona_management.py services/control-plane/bff/test_read_store_deployment.py services/control-plane/bff/test_read_store_incident.py services/control-plane/bff/test_w3_surfaces.py services/control-plane/bff/test_w4_remaining_catalog.py`
  - Result: `6 passed`
- Direct HTTP probe with `fastapi.testclient.TestClient`:
  - `/api/v1/consultations/cs-resp-20260410-001/participants` -> `200` with `2` participants
  - `/api/v1/consultations/cs-resp-20260410-001/outcome` -> `200` with `root_session_id = "cs-20260410-001"` and `outcome = "conditional"`
  - `/api/v1/consultations/cs-resp-20260410-001/evidence` -> `200` with `2` evidence refs
  - `/api/v1/personas/p-risk-analyst` -> `200`
