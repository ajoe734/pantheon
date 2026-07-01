# LOOP-AUTO-DEP-004 Evidence

Task: split promotion and deployment BFF truth by stage.

## Delivered Surface

- BFF deployment detail/list payloads now expose `stage_truth` with separate
  stages for:
  - `approval`
  - `plan`
  - `saga`
  - `binding`
  - `runtime_fleet`
- `/bff/deployments`, `/bff/deployments/{id}`,
  `/api/v1/deployment-plans/{id}`, and
  `/api/v1/operator/deployment-review/{id}` include the same stage projection.
- `meta.surfaces` now includes stage-specific source health such as
  `approval_stage`, `saga_stage`, `binding_stage`, and `runtime_fleet_stage`.
- `/bff/deployments` list-level stage surfaces aggregate the listed page's
  deployment plans, so a healthy first item cannot hide a later plan with
  missing runtime fleet evidence.
- Saga DLQ/blocking state remains visible as the saga stage failure instead of
  being hidden behind an approved deployment plan.
- Runtime fleet stage only becomes `active` or `observed` when runtime-owned
  monitoring or telemetry evidence exists. It does not infer runtime health
  from deployment plan metadata or RuntimeBinding existence.

## Verification

```bash
python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/test_loop_auto_dep004_stage_truth.py
```

Result: passed.

```bash
pytest services/control-plane/bff/test_loop_auto_dep004_stage_truth.py -q
```

Result: owner run `2 passed, 4 warnings in 4.24s`; reviewer follow-up after
list-surface aggregation coverage `3 passed, 4 warnings in 5.92s`.

```bash
pytest services/control-plane/bff/test_loop_auto_dep004_stage_truth.py services/control-plane/bff/test_read_store_deployment.py services/control-plane/bff/test_pkt004_deployment_approval_drilldowns_contract.py services/control-plane/bff/test_bff_runtimes_contract.py services/control-plane/bff/tests/test_bff_b2_list_detail_facade.py -q
```

Result: `47 passed, 16 warnings in 20.78s`; post-rebase rerun on
`origin/dev` also passed with `47 passed, 16 warnings in 19.21s`. Reviewer
follow-up after list-surface aggregation coverage passed with
`48 passed, 16 warnings in 20.20s`.

Additional non-blocking observation:

```bash
pytest services/control-plane/bff/test_loop_auto_dep004_stage_truth.py services/control-plane/bff/test_read_store_deployment.py services/control-plane/bff/test_pkt004_deployment_approval_drilldowns_contract.py services/control-plane/bff/test_bff_governance_runtime_risk_audit_contract.py services/control-plane/bff/test_bff_runtimes_contract.py -q
```

Result: `10 passed, 2 failed`. The failures were existing deprecated action
route expectations in `test_bff_governance_runtime_risk_audit_contract.py`
expecting `202` for `/bff/deployments/{id}/actions/*` and
`/bff/incidents/{id}/actions/*`; the BFF currently returns `410` with a
replacement route. DEP-004 did not change those action routes.

## Safety Boundary

No live-capital execution, approval bypass, runtime-manager write path, or
deployment service mutation changed in this task. The change is a BFF
read-projection split and contract coverage for operator-visible truth.
