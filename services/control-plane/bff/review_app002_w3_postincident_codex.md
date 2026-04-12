# APP-002-W3-POSTINCIDENT-EVOLUTION — Codex Review

## Summary
- EV-01–EV-04, LN-01, TL-03 endpoints added with list/detail filters and read-store seed data.
- Post-incident composed view wired but has two correctness gaps (telemetry + lineage) that need fixes before approval.

## Blocking Issues
1. **Post-incident telemetry uses the wrong source + wrong key**
   - `get_post_incident_review()` fetches `incident.get("affected_persona_id")` (field does not exist) and then calls `get_telemetry_summary()`.
   - Contract requires TL-03 (performance chart by `artifact_id`), not TL-02 summary. This currently yields `None` and degrades every post-incident response.
   - Fix: use `artifact_id = incident.get("artifact_id")` and `read_store.get_telemetry_performance(artifact_id)`; set `telemetry_performance` to that payload.

2. **LN-01 is stubbed but reported as OK**
   - `lineage_edges` is hardcoded to `[]` with `status: ok`. This misrepresents the surface when no lineage is actually fetched.
   - Fix: call `read_store.list_lineage_edges(artifact_id=artifact_id)` (artifact from incident) or mark the surface as `degraded/unverifiable` if intentionally unavailable.

## Verification
- Not run (review-only). No new tests were added for EV/LN/TL endpoints.

## Notes / Follow-ups (non-blocking)
- RBAC: Contract allows `viewer` for TL-03 / LN-01, but these endpoints require operator-level roles via `_require_read_role`. If viewer access is intended, add `viewer` to `_READ_ROLES` or gate per-surface.
