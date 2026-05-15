# PKT-002 Incident Action Drawer — Pantheon Delivery Note

## Status

`delivered`

## Summary

Pantheon repaired the reopened PKT-002 Incident Action Drawer BFF gap on
`POST /api/v1/operator/commands`.

The drawer write surface now accepts the published PKT-002 command envelope for:

- `PauseExecution`
- `IssueRiskOff`
- `LiquidateAll`
- `HardRollback`
- `IssueSafeMode`

The repaired BFF now:

1. accepts `request.command` values from the published PKT-002 contract
2. accepts `target.type = Runtime`
3. no longer requires a separate `request.action` field for drawer commands
4. validates the published PKT-002 `params` blocks at the request boundary
5. queues the published envelope directly and resolves Pantheon-owned runtime
   routing after the queue boundary instead of forcing UI-side translation

For `PauseExecution` and `HardRollback`, execution keeps using the existing
Pantheon internal command paths. For `IssueRiskOff`, `LiquidateAll`, and
`IssueSafeMode`, runtime-to-pool resolution now happens inside the worker; when
that context is absent in the local environment, the command produces an
explicit failed receipt later instead of returning a submission-time `422`.

## Coordination Outcome

- Pantheon write-surface gap: resolved
- Contract-ready packet: refreshed
- Lovable UI task packet: refreshed for the resumed front-end cycle
- Front-end follow-up: resume implementation against the published PKT-002
  drawer contract; no UI-side command translation is authorized

## Verification

- Updated the Pantheon BFF request model and queue boundary:
  - `services/control-plane/bff/models.py`
  - `services/control-plane/bff/main.py`
  - `services/control-plane/bff/command_executor.py`
  - `services/control-plane/bff/smoke_test_incident.py`
- Verified the repaired PKT-002 write surface with direct TestClient-backed smoke coverage:
  - `python3 -m pytest services/control-plane/bff/smoke_test_incident.py -q -k 'pause_execution or issue_risk_off or liquidate_all or hard_rollback or issue_safe_mode'`
  - Result: `5 passed`
- Re-ran the command executor suite:
  - `python3 -m pytest services/control-plane/bff/test_command_executor.py -q`
  - Result: `13 passed`

## Notes

- `GET /api/v1/kill-switch/status` remained aligned throughout this repair and
  did not require contract changes.
- The local workspace still has broader incident read-surface fixture gaps
  outside this specific drawer write fix. They do not block republishing the
  PKT-002 drawer command contract because the drawer’s blocking issue was the
  submission-side schema mismatch.
- When local runtime metadata is missing, `IssueRiskOff`, `LiquidateAll`, and
  `IssueSafeMode` now fail after queueing with an explicit routing error instead
  of rejecting the request envelope itself.

## Next Follow-up

- Front repo: resume the PKT-002 Incident Action Drawer cycle from the refreshed
  contract-ready and Lovable UI task packets
- Runtime/live QA: validate end-to-end command execution against a running
  Pantheon command backend once the target environment is available
