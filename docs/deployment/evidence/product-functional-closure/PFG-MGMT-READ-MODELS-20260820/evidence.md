# Read Model Evidence Manifest: PFG-MGMT-READ-MODELS-20260820

- Task: PFG-MGMT-READ-MODELS-20260820
- Owner: Antigravity
- Reviewer: Codex2

## Summary of Changes
Delivered backend read model endpoints to replace synthetic Management console data:
1. `GET /bff/management/formula-jobs` - Real Formula execution/evaluation jobs read model with job status, metrics, chart lineage, source identity, and freshness.
2. `GET /bff/management/activity` - System activity read model projecting canonical audit events.
3. `GET /bff/management/paper-telemetry` - Strategy paper execution telemetry and equity series read model.
4. `GET /bff/management/postmortems` & `GET /bff/management/postmortems/{postmortem_id}` - Postmortem incident analysis list and detail read models.
5. Registered `formula_jobs`, `activity_audit`, and `paper_telemetry` datasets in `ServiceBackedReadAdapter._DATASETS` to ensure `self._service.list_records` resolves canonical keys instead of raising `KeyError`. Fixed `postmortems` dataset file search targets (`postmortems.json`, `incidents.json`).
6. Added safe normalization mapping for canonical incident postmortem schema fields (`incident_evidence_summary` -> `impact_summary`, string action items -> dict action items, `deployment_stage` -> `severity`).

## Verification & Contract Tests
- Ran `services/control-plane/bff/tests/test_management_real_read_models.py` (6/6 passed).
- Verified non-mock file-backed `ServiceBackedReadAdapter` readback with canonical postmortem schema mapping and freshness attribution.
- Verified typed empty / unavailable / degraded envelope structures with source attribution (`source_identity`, `freshness`, `degradation` reason).
- Verified `git diff --check` passes cleanly without trailing whitespace.
