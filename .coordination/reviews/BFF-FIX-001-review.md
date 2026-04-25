# BFF-FIX-001 Review Packet

## Date

2026-04-18

## Reviewer

Codex

## Findings

No blocking findings.

The five BFF-gap acceptance items are satisfied by the current Pantheon BFF implementation and local verification evidence.

## Verified Evidence

1. `PKT-002-incident-action-drawer`
   - `services/control-plane/bff/models.py` accepts the published PKT-002 command names (`PauseExecution`, `IssueRiskOff`, `LiquidateAll`, `HardRollback`, `IssueSafeMode`) and `target.type = Runtime`.
   - `services/control-plane/bff/models.py` keeps `action` optional, so the published drawer contract is accepted without a required legacy `action` field.
   - `python3 -m pytest services/control-plane/bff/smoke_test_incident.py -q`
     passed with the incident command schema checks for all five PKT-002 drawer actions.

2. `PKT-002-incident-detail`
   - `services/control-plane/bff/main.py` now projects contract-shaped `data.affected_bindings[]`, `data.kill_switch`, `allowedActions`, `meta.surfaces.incident`, `meta.surfaces.affected_bindings`, `meta.surfaces.kill_switch`, and `meta.surfaces.allowedActions`.
   - `services/control-plane/bff/main.py` maps severity values to `sev1`/`sev2`/`sev3` and resolves `opened_at` from `opened_at` or `created_at`.
   - `python3 -m pytest services/control-plane/bff/smoke_test_incident.py -q`
     passed with `test_composed_incident_response`, confirming the contract-facing detail shape.

3. `PKT-002-incident-home`
   - `services/control-plane/bff/main.py` returns `GET /api/v1/incidents` with `items`, `page_info.next_page_token`, `meta.snapshot_at`, and `meta.surfaces.incident_list`.
   - `services/control-plane/bff/main.py` returns `GET /api/v1/kill-switch/status` with the `kill_switch` wrapper, canonical status fields, `allowedActions`, `meta.snapshot_at`, and `meta.surfaces.kill_switch`.
   - `python3 -m pytest services/control-plane/bff/smoke_test_incident.py -q`
     passed with `test_in01_incident_list`, `test_in01_incident_list_filtered`, and `test_in05_kill_switch_status`.

4. `PKT-003-post-incident-review`
   - `services/control-plane/bff/main.py` includes `resolved_at` in `_project_incident_home_item()`.
   - `python3 -m pytest services/control-plane/bff/smoke_test_incident.py -q`
     passed with `test_in01_resolved_incident_list_includes_resolved_at`.

5. `PKT-004-capital-binding-drilldowns`
   - `services/control-plane/bff/main.py` exposes `persona_id` on `GET /api/v1/bindings`.
   - `services/control-plane/bff/read_store.py` applies `persona_id` filtering before returning rows.
   - `python3 -m pytest services/control-plane/bff/test_pkt004_capital_binding_drilldowns_contract.py -q`
     passed (`2 passed`), covering both route-level and store-level filtering.

6. Coordination artifacts
   - All five BFF-gap request packets under `.coordination/requests/` are marked `resolved: true`.
   - The corresponding contract-ready / lovable-ui-task response artifacts exist under `.coordination/responses/`.

## Decision

`BFF-FIX-001` is approved.

The original five-gap BFF closure scope is complete. The remaining incident runtime-data follow-up is already split into `RUNTIME-FIX-001` and does not block this task's BFF-gap acceptance.

## Residual Risk

- `services/control-plane/bff/smoke_test_incident.py` emits Pydantic v2 deprecation warnings for `.dict()` usage in error and command queue paths. This is not a blocker for the reviewed contract behavior, but it should be cleaned up before a future Pydantic v3 upgrade.
