# PKT-002 Incident Action Drawer — Contract Lock

## Status

`delivered`

## Lock Date

2026-04-17

## Source Payload

`.coordination/requests/PKT-002-incident-action-drawer-bff-gap.yaml`

## Version Lock

- Backend commit reference: `e79b7ae669c99bd1a40f7ae095d8d76f3c05fb01`
- BFF contract version: `pantheon-bff@e79b7ae669c99bd1a40f7ae095d8d76f3c05fb01`

## Delivered Endpoints

- `GET /api/v1/kill-switch/status`
- `POST /api/v1/operator/commands`

## Contract Paths

- `docs/bff/PKT-002-incident-action-drawer.md`
- `docs/examples/PKT-002-incident-action-drawer.json`
- `docs/pantheon-handoffs/PKT-002-incident-action-drawer/FRONTEND_CHANGE_SPEC.md`

## Delivery Guarantees

- `GET /api/v1/kill-switch/status` now returns:
  - `kill_switch`
  - `allowedActions`
  - `meta.snapshot_at`
  - `meta.surfaces.kill_switch`
  - `meta.surfaces.allowedActions`
- `POST /api/v1/operator/commands` now returns a flat receipt with:
  - `receipt_id`
  - `command`
  - `status`
  - `accepted_at`
  - `routing_path`
  - `expected_completion_at`
  - `error_message`
- `POST /api/v1/operator/commands` now accepts the published PKT-002 request
  envelope for:
  - `PauseExecution`
  - `IssueRiskOff`
  - `LiquidateAll`
  - `HardRollback`
  - `IssueSafeMode`
- PKT-002 drawer writes use:
  - `target.type = Runtime`
  - no separate `request.action` field
  - contract-published `params` blocks only
- Direct TestClient validation passed for the repaired write surface:
  - `python3 -m pytest services/control-plane/bff/smoke_test_incident.py -q -k 'pause_execution or issue_risk_off or liquidate_all or hard_rollback or issue_safe_mode'`

## Follow-up Scope

- Reuse the published Pantheon contract and example payload without inventing
  new endpoints or UI-side request translation
- Resume the front-end implementation cycle from the refreshed PKT-002 packet
  family
- Run live browser and command-path QA against a running Pantheon BFF
