# APP-002-W2-READ-INCIDENT — Codex Review

## Summary
- Aligned incident/postmortem seed data with canonical IncidentCase/Postmortem schemas (incident_id, binding_id, deployment evidence, runtime_id, trace_id).
- Enforced admin-only RBAC for kill-switch status (IN-05) per BFF_API_CONTRACT.md.
- Updated composed views to resolve runtime binding via binding_id and postmortem via incident_id.
- Added time_range passthrough for postmortem list (store still returns all; deferred filter implementation).

## Verification
- PASS: `python3 services/control-plane/bff/test_read_store_incident.py`
- FAIL (env): `python3 services/control-plane/bff/smoke_test_incident.py` -> `ModuleNotFoundError: No module named 'fastapi'`

## Notes / Follow-ups
- Incident response composed view still includes `evolution_decisions` even though the contract lists EV-04 (rollbacks). If we need strict response parity, consider either documenting the extra field or aligning to EV-04-only payload.
- Postmortem list `time_range` filter remains deferred in `ReadSurfaceStore.list_postmortems`.
