# RT-004: Runtime Deploy/Pause/Replace/Rollback Actions Acceptance Evidence

**Task**: RT-004 Runtime deploy/pause/replace/rollback actions
**Owner**: Codex
**Reviewer**: Claude2
**Date**: 2026-05-16
**Scope source**: `docs/04/pantheon_sa_supplemental_2026-05-15/GAP_dev_team_master_rebaseline_2026-05-15.md` section 5.8

## Acceptance Scope

RT-004 covers the runtime-manager action lane after RT-002 established the
runtime-manager skeleton:

- deploy an approved/executing `DeploymentPlan` descriptor into a
  `RuntimeBinding`
- pause a runtime binding through the guarded runtime status state machine
- replace a runtime binding with a governed replacement artifact
- execute canonical rollback strategies while preserving binding and position
  lineage

The deployable service surface is `services/runtime-manager/`. The canonical
runtime binding object and write-authority contract remain in
`services/execution/runtime-manager/`.

## Action Surface

| Capability | Route / API | Verification coverage |
|---|---|---|
| Deploy | `POST /api/runtimes/deploy`, `RuntimeManagerService.deploy()`, `RuntimeManagerClient.deploy()` | creates active `RuntimeBinding` from approved/executing plan descriptor |
| Pause | `POST /api/runtime-bindings/<binding_id>/transition` with `pending_pause` then `paused`; legacy BFF dispatch path `POST /api/internal/v1/runtimes/<binding_id>/pause` | canonical readback shows the same binding reaches `paused` |
| Replace | `POST /api/rollback` with `action_type=replace`, `RuntimeManagerService.rollback()` | creates replacement binding, retires old binding after cutover, records `rollback_parent` |
| Rollback | `POST /api/rollback` with `replace`, `pause_then_replace`, or `liquidate_then_replace`; `GET /api/rollback/history` | preserves rollback action type and position lineage; exposes replacement history by pool |

## Guardrails Verified

- RuntimeBinding writes remain inside Runtime Manager.
- Deployment requires `plan_status` in `approved` or `executing`.
- Deployment requires `persona_capital_binding_status=active`.
- Deployment requires `allowed_deployment_scope` to permit the target stage.
- Deployment requires `loader_checks_passed=True`.
- Pause follows `active -> pending_pause -> paused`.
- Replace creates the replacement binding before retiring the old binding.
- Rollback replacement records `rollback_parent` and `rollback_action_type`.
- Position lineage keeps `opened_by_artifact_id` immutable and updates
  `current_managed_by_binding_id` only after cutover.
- Legacy internal operator routes share the same in-process runtime-manager
  service/store as canonical `/api/...` routes.

## Verification

Focused runtime action suite:

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/pantheon-rt004-venv/bin/python -m pytest services/runtime-manager/test_runtime_manager.py -k 'RuntimeManagerServiceTests or RuntimeManagerClientTests or RuntimeManagerHttpRouteTests' -q
```

Result: `16 passed, 35 deselected in 7.34s`.

Deployable internal command surface:

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/pantheon-rt004-venv/bin/python -m pytest services/runtime-manager/test_internal_api_routes.py -q
```

Result: `6 passed in 6.55s`.

Syntax check:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/runtime-manager/service.py services/runtime-manager/main.py services/runtime-manager/runtime_manager_client.py services/runtime-manager/test_runtime_manager.py services/runtime-manager/test_internal_api_routes.py
```

Result: passed with no output.
