# DEP-004 Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `DEP-004-SIDECAR-ACCEPTANCE`
**Helper parent:** `DEP-004` - pool x runtime compatibility check before deployment advance
**Owner:** `Codex`
**Reviewer:** `Claude2`
**Prepared by:** `Codex`
**Date:** `2026-05-18`
**Packet status:** `ready_for_review`

> Scope constraint: support artifact only. This packet does not change L1
> canonical truth, core contract truth, runtime registries, or the DEP-004
> implementation. It packages the acceptance checklist, dependency map, and
> focused verification evidence for reviewer and parent-owner absorption.

## 1. Purpose

`DEP-004` closes the governance-to-runtime gap where a `DeploymentPlan` could
advance toward runtime binding without a read-only compatibility check between:

- the target `capital_pool`
- the `DeploymentPlan` target size and stage
- runtime broker requirements
- the active `PersonaCapitalBinding`

This sidecar makes the DEP-004 surface reviewable without reopening canonical
policy. The parent owner can use it to decide whether the implemented guard and
cron deploy hook are ready to absorb into the main DEP-004 review path.

## 2. Parent Implementation Snapshot

Observed parent implementation commit:

```text
89cdebb6 DEP-004: add pool runtime compatibility guard
```

Task-owned parent files present in the current branch:

| File | Role |
|---|---|
| `services/control-plane/governance/pool_runtime_compat.py` | Read-only guard and `CompatibilityResult` dict producer |
| `services/control-plane/governance/test_pool_runtime_compat.py` | Unit coverage for pass path, five required failure checks, and raising wrapper |
| `services/control-plane/governance/pool_runtime_compat_contract.md` | Contract summary, returned shape, rejection reasons, and non-goals |
| `services/control-plane/cron/service.py` | Deploy workflow hook before execution projection and saga bootstrap |
| `services/control-plane/cron/test_cron.py` | Integration coverage that failed compatibility blocks deploy and passed compatibility is recorded |

Adjacent but not owned by DEP-004:

| Surface | Why it is adjacent only |
|---|---|
| `services/deployment/` pool/runtime preflight API | Existing CAP-002-RB style read-only service surface. DEP-004 does not replace or refactor it. |
| Capital and persona binding stores | DEP-004 reads supplied records/stores but does not mutate ownership or binding state. |
| Runtime manager / RuntimeBinding implementation | DEP-004 blocks before RuntimeBinding advance; it does not create or rewrite RuntimeBinding records. |

## 3. Acceptance Checklist

| Parent criterion | Evidence | Result |
|---|---|---|
| `pool_runtime_compat.py` exposes `check_compatibility(capital_pool_id, deployment_plan_id)` | Public API accepts those ids and optional records/stores, returning a plain dict via `CompatibilityResult.to_dict()` | Met |
| Result includes `passed: bool` and `rejection_reasons: list` | `CompatibilityResult` has `passed`, `rejection_reasons`, and `details`; `_result()` sets `passed` false when reasons exist | Met |
| Pool admissibility status equals `active` | Guard reads pool `status` and appends `pool_admissibility_status_not_active` when not active | Met |
| Pool risk budget covers `DeploymentPlan` target size | Guard reads pool budget aliases and plan target aliases, rejects missing or insufficient budget | Met |
| Pool jurisdiction matches runtime broker jurisdiction | Guard normalizes pool jurisdiction(s) and broker jurisdiction, rejects missing or mismatched values | Met |
| Runtime mode matches deployment stage | Guard normalizes runtime mode and plan target stage, rejects invalid stage or mismatch | Met |
| `PersonaCapitalBinding` exists and is active | Guard accepts a binding or resolves one from store, rejects missing, non-active, pool mismatch, and persona mismatch cases | Met |
| Deployment advance refuses failed compatibility | Cron deploy calls `enforce_compatibility(...)` after plan creation and before projection/saga bootstrap; failed result raises `DeploymentPlanError` | Met |
| Tests cover one pass plus five required fail scenarios | Unit test has one pass path and parametrized failures for status, budget, jurisdiction, mode, and binding state | Met |
| No live broker side effects | Guard is read-only and cron tests run locally; no LEAN/live broker path is invoked | Met |

## 4. Dependency Map

### Formal upstream tasks from the sidecar brief

| Dependency | State in task brief | Why DEP-004 depends on it |
|---|---|---|
| `DEP-001` | done | Supplies first-class `DeploymentPlan` and stage planner semantics. DEP-004 checks the plan after creation. |
| `DEP-002` | done | Supplies deployment saga consistency. DEP-004 runs before saga bootstrap so rejected deploys do not emit saga/outbox work. |
| `CAP-001` | done | Defines `capital_pool` and `PersonaCapitalBinding` objects, active status, and binding ownership semantics. |
| `RT-001` | done | Defines RuntimeBinding identity/stage context that DEP-004 protects before runtime advance. |

### Effective call chain

| Step | Evidence | DEP-004 interpretation |
|---|---|---|
| Deploy workflow builds `DeploymentPlan` | `CronOrchestrator._run_deploy()` calls `StagePlanner.create_plan(...)` | Compatibility uses a real plan object, not loose request fields only. |
| Compatibility context is extracted | `_compatibility_context()` reads `pool_runtime_compat` or `pool_runtime_compatibility` payload context | Callers can supply explicit records/stores while the hook stays read-only. |
| Guard runs before projection | `enforce_compatibility(...)` executes before `build_execution_projection(...)` | A failed guard blocks execution projection and saga bootstrap. |
| Passed result is recorded | Deploy request includes `pool_runtime_compatibility` when the hook runs | Downstream reviewers can audit the pass evidence in the deployment request. |

## 5. Verification Snapshot

Executed in this sidecar session:

```bash
python3 -m pytest -q services/control-plane/governance/test_pool_runtime_compat.py services/control-plane/cron/test_cron.py
```

Observed result:

```text
21 passed in 2.15s
```

What this verifies:

- guard output shape and pass path
- five required rejection categories from the DEP-004 acceptance list
- `enforce_compatibility()` raises with rejection reason text
- cron deploy refuses a failed pool/runtime compatibility context
- cron deploy records a passed compatibility result on the deployment request

Not run in this sidecar:

- full repository test suite
- live broker, LEAN runtime, or external service smoke

Those are intentionally outside this support-only acceptance packet.

## 6. Reviewer Focus

Recommended reviewer checks for `Claude2`:

1. Confirm this packet remains support-only and does not redefine DEP-004
   canonical truth.
2. Confirm the checklist maps exactly to the parent DEP-004 acceptance criteria.
3. Confirm the cron hook is placed before execution projection and saga
   bootstrap, preserving fail-closed behavior.
4. Confirm the adjacent `services/deployment/` compatibility preflight remains
   out of scope for this DEP-004 sidecar.

## 7. Recommended Disposition

Recommended reviewer action:

- approve `DEP-004-SIDECAR-ACCEPTANCE` if the packet accurately reflects the
  current branch evidence
- let the DEP-004 parent owner decide whether to absorb this support packet into
  the parent review/closeout trail

Suggested handoff summary:

> Support-only acceptance packet prepared for DEP-004. It maps the pool/runtime
> guard, cron deploy hook, dependency chain, and focused verification evidence.
> Focused pytest for the guard and cron deploy hook passed: 21 tests. No
> canonical truth or core implementation files were changed by this sidecar.
