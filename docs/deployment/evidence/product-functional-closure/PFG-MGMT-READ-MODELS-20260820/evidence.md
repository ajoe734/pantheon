# Read Model Evidence Manifest: PFG-MGMT-READ-MODELS-20260820

- Task: PFG-MGMT-READ-MODELS-20260820
- Owner: Antigravity2
- Reviewer: Codex2

## Summary of Changes
Delivered backend read model endpoints to replace synthetic Management console data while reusing canonical read owners and stores:
1. `GET /bff/management/formula-jobs` - Real Formula execution/evaluation jobs read model projecting from canonical `jobs` (and `formula_jobs` store) with job status, metrics, chart lineage, source identity, and freshness.
2. `GET /bff/management/activity` - System activity read model consolidating canonical `governance_audit_events`, `telemetry_events`, and `activity_audit` event stores.
3. `GET /bff/management/paper-telemetry` - Strategy paper execution telemetry and equity series read model projecting from canonical `telemetry_events`, `runtime_bindings`, and `paper_telemetry` stores.
4. `GET /bff/management/postmortems` & `GET /bff/management/postmortems/{postmortem_id}` - Postmortem incident analysis list and detail read models projecting from canonical `postmortems` store.
5. Reused exact canonical read owners (`jobs`, `governance_audit_events`, `telemetry_events`, `runtime_bindings`, `postmortems`) with zero synthetic fallback or duplicate parallel stores.
6. Safe normalization mapping for canonical incident postmortem schema fields (`incident_evidence_summary` -> `impact_summary`, string action items -> dict action items, default severity).
7. Cleaned whitespace and trailing newlines to ensure `git diff --check` passes cleanly.

## Verification & Contract Tests
- Ran `services/control-plane/bff/tests/test_management_real_read_models.py` (9/9 passed).
- Verified canonical dataset projection for `jobs`, `governance_audit_events`, `telemetry_events`, `runtime_bindings`, and `postmortems`.
- Verified non-mock file-backed `ServiceBackedReadAdapter` readback with canonical postmortem schema mapping and freshness attribution.
- Verified typed empty / unavailable / degraded envelope structures with source attribution (`source_identity`, `freshness`, `degradation` reason).
- Verified `git diff --check origin/dev` passes cleanly with zero whitespace errors.
