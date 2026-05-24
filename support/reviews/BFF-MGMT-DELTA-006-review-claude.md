# Review: BFF-MGMT-DELTA-006 — GET /bff/management/incident-timeline

Reviewer: Claude
Date: 2026-05-24
Status: **Approved**

## Scope Reviewed

Route, envelope, severity bucket, filter, auth, CORS preflight, and execute-plans TypeScript wiring.

## Findings

### Route / Envelope (✓)

- `GET /bff/management/incident-timeline` is registered at `main.py:23018`.
- Response envelope conforms to spec: `data`, `items`, `rows`, `incidents`, `events`, `summary`, `severityBuckets`, `page_info`, `meta`.
- `data.id == "management-incident-timeline"`. ✓
- Top-level `items`, `rows`, `incidents`, `events` are aliased correctly to `data.items`. ✓
- `meta.policy == "read_only_incident_timeline"`. ✓
- `meta.composition_sources` includes `"GET /bff/incidents"`. ✓

### Chronological Sort (✓)

- `_management_incident_sort_key` sorts by `occurred_at` → `created_at` → `opened_at` ascending by default.
- `sort_order` parameter flips to descending when set to `"desc"`.
- `first_incident_at` / `latest_incident_at` are correctly swapped for descending sorts.
- `sequence` is assigned post-sort (1-based). ✓

### Severity Bucket Behavior (✓)

- `_MANAGEMENT_INCIDENT_HIGH_SEVERITIES = {"critical", "high", "sev1", "sev2", "p0", "p1"}` — `"critical"` → `"high"`. ✓
- `_MANAGEMENT_INCIDENT_MEDIUM_SEVERITIES = {"medium", "moderate", "warning", "warn", "sev3", "p2"}`. ✓
- Fallback: everything else → `"low"`. ✓
- Bucket counts use pre-pagination `items`, not `page_items`. ✓
- `severityBuckets` exposed at both top-level and under `data`. ✓

### lineage_ref (✓)

- `_project_bff_incident_case` (line 33094) builds `lineage_ref = f"{artifact_id}@{artifact_version}"` when not already set.
- `_management_incident_timeline_item` reads `payload.get("lineage_ref")` and exposes it as `lineageRef` / `lineage_ref`. ✓
- Test fixture with `artifact_id="artifact-alpha"`, `artifact_version="v1"` produces `lineage_ref == "artifact-alpha@v1"`. ✓

### sourceRefs (✓)

- `sourceRefs` includes `incidentIds`, `runtimeIds`, `deploymentPlanIds`, `capitalPoolIds`, `personaCapitalBindingIds`, `artifactIds`, `telemetryEventIds`. ✓
- Camel-case and snake_case forms both present. ✓

### Auth (✓)

- `_require_read_role(identity)` raises HTTP 401 for missing/invalid `Authorization`. ✓

### CORS Preflight (✓)

- `OPTIONS /bff/management/incident-timeline` returns 204 with matching `access-control-allow-origin`. ✓

### execute-plans TypeScript Wiring (✓)

- `ManagementIncidentTimelineQuery` covers all BFF query params including `affected_pool_id` alias and `sort_order`. ✓
- `ManagementIncidentTimelineItem` includes all response fields. ✓
- `ManagementIncidentTimelineResponse` envelope matches the BFF contract. ✓
- `managementIncidentTimelinePath(query)` helper is exported. ✓

## Verification

```
pytest -q services/control-plane/bff/test_bff_management_delta_routes.py \
          services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py \
          services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py \
          services/control-plane/bff/tests/test_auth_jwks_strict.py
```

Result: **75 passed, 3 warnings** (existing `datetime.utcnow()` deprecation in `read_store.py` — pre-existing, not introduced by this task).

## Acceptance Criteria Verification

| # | Criterion | Verified |
|---|---|---|
| 1 | Path registered in FastAPI/OpenAPI | ✓ |
| 2 | Incident rows are chronologically sorted | ✓ |
| 3 | Severity buckets expose `high`, `medium`, and `low` counts | ✓ |
| 4 | Anonymous request returns HTTP 401 | ✓ |
| 5 | Authenticated request returns HTTP 200 | ✓ |
| 6 | Response keeps canonical aggregate envelope | ✓ |
| 7 | CORS preflight returns HTTP 204 | ✓ |
| 8 | Focused pytest cases cover `incident_timeline` success, filter, auth, and preflight | ✓ |
| 9 | execute-plans exposes typed path and fetch helpers | ✓ |

## Decision

**Approved.** Implementation satisfies all 9 acceptance criteria. Route/envelope/severity bucket/lineage/auth/CORS behavior all verified. Returned to owner (Codex) for closeout.
