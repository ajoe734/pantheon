# RT-002: Runtime Manager Skeleton Acceptance Evidence

**Task**: RT-002 Runtime Manager skeleton
**Owner**: Codex
**Reviewer**: Claude (reassigned from Codex2 after quota failure)
**Date**: 2026-05-16
**Scope source**: `docs/04/pantheon_sa_supplemental_2026-05-15/GAP_dev_team_master_rebaseline_2026-05-15.md` section 7.2

## Acceptance Scope

RT-002 acceptance is the P1 skeleton requirement:

- runtime inventory API is available
- runtime bind API is available
- runtime status API is available
- an approved/executing paper `DeploymentPlan` descriptor can create a `RuntimeBinding`

The deployable service surface is `services/runtime-manager/`. The canonical
runtime binding object and write-authority contract remain in
`services/execution/runtime-manager/`.

## Runtime Manager Surface

| Capability | Route / API | Verification coverage |
|---|---|---|
| Bind approved plan descriptor to runtime | `POST /api/runtimes/deploy` and `RuntimeManagerService.deploy()` | creates active `RuntimeBinding` with `plan_id`, `deployment_mode`, `persona_capital_binding_id` |
| Runtime inventory | `GET /api/runtime-bindings`, `RuntimeManagerService.list_all()`, `list_by_pool()`, `list_by_plan()` | list by all, pool, and plan |
| Runtime status | `GET /api/runtime-bindings/<binding_id>`, `RuntimeManagerService.get()` | reads single binding status |
| Active runtime lookup | `GET /api/runtimes/<pool_id>/active`, `RuntimeManagerService.get_active_for_pool()` | returns the single active binding per pool |

RT-004 owns the broader deploy/pause/replace/rollback action lane. Those routes
already exist in the same service, but RT-002 review should focus on the
skeleton inventory/bind/status surface above.

## Guardrails Verified

- `plan_status` must be `approved` or `executing`
- `persona_capital_binding_status` must be `active`
- `allowed_deployment_scope` must permit `target_stage`
- `loader_checks_passed` must be explicitly `True`
- `target_stage` must be a valid `DeploymentMode`
- `RuntimeBindingStore.create()` enforces the single-runtime rule per capital pool
- `RuntimeManagerService` remains the mutation path over `RuntimeBindingStore`

## Verification

System Python service/client slice:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/runtime-manager/test_runtime_manager.py -k 'RuntimeManagerServiceTests or RuntimeManagerClientTests' -q
```

Result: `12 passed, 38 deselected in 3.45s`.

System Python non-HTTP runtime-manager slice:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/runtime-manager/test_runtime_manager.py -k 'not HttpRoute' -q
```

Result: `40 passed, 10 deselected in 10.74s`.

Syntax check:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/runtime-manager/service.py services/runtime-manager/main.py services/runtime-manager/runtime_manager_client.py
```

Result: passed with no output.

Full HTTP route suite in isolated venv:

```bash
python3 -m venv /tmp/pantheon-rt002-venv
/tmp/pantheon-rt002-venv/bin/python -m pip install -r services/runtime-manager/requirements.txt pytest
PYTHONDONTWRITEBYTECODE=1 /tmp/pantheon-rt002-venv/bin/python -m pytest services/runtime-manager/test_runtime_manager.py -q
```

Result: `50 passed in 11.73s`.

Note: the first full-suite attempt under system Python failed only because the
externally managed host environment did not have `flask` installed. The full
suite passes when run with the service's declared requirements in `/tmp`.

## Review Focus

- `services/runtime-manager/service.py`
- `services/runtime-manager/main.py`
- `services/runtime-manager/runtime_manager_client.py`
- `services/runtime-manager/test_runtime_manager.py`
- `services/execution/runtime-manager/runtime_binding.py`
- `services/execution/runtime-manager/contract.md`

## Owner Closeout Verification

Before finalizing from `review_approved` to `done`, Codex re-ran the reviewer
focused verification in the current worktree:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/runtime-manager/test_runtime_manager.py -k 'RuntimeManagerServiceTests or RuntimeManagerClientTests' -q
```

Result: `12 passed, 39 deselected in 6.58s`.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/runtime-manager/test_runtime_manager.py -k 'not HttpRoute' -q
```

Result: `40 passed, 11 deselected in 16.66s`.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/runtime-manager/service.py services/runtime-manager/main.py services/runtime-manager/runtime_manager_client.py
```

Result: passed with no output.
