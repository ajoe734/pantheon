# RT-001 Review: RuntimeBinding schema

**Task:** RT-001 — RuntimeBinding schema
**Reviewer:** Claude2
**Owner:** Claude
**Date:** 2026-05-16
**Verdict:** APPROVED

---

## Reviewer Environment Verification

```
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/execution/runtime-manager/test_runtime_binding.py -q
45 passed in 9.04s

python3 services/execution/runtime-manager/smoke_test_runtime_binding.py
Results: 10/10 test groups passed — ALL PASS

python3 -m py_compile services/execution/runtime-manager/runtime_binding.py
# exit 0 — py_compile OK
```

---

## Schema Coverage

All 10 required fields present in `RuntimeBinding` dataclass and `runtime_binding.schema.json`:

| Field | Required | Verified |
|---|---|---|
| `binding_id` | ✓ | ✓ |
| `runtime_id` | ✓ | ✓ |
| `capital_pool_id` | ✓ | ✓ |
| `artifact_id` | ✓ | ✓ |
| `artifact_version` | ✓ | ✓ |
| `deployment_mode` | ✓ | ✓ |
| `effective_at` | ✓ | ✓ |
| `status` | ✓ | ✓ |
| `plan_id` | ✓ | ✓ |
| `persona_capital_binding_id` | ✓ | ✓ |

Optional fields also confirmed: `retired_at`, `rollback_parent`, `rollback_action_type`, `metadata`.

---

## Contract Invariants

| Invariant | Contract Ref | Verified |
|---|---|---|
| `plan_id` required (no binding without DeploymentPlan) | §4 | ✓ |
| `persona_capital_binding_id` required (governance admissibility proof) | §4 | ✓ |
| `deployment_mode` ∈ {paper, canary, live, frozen} | §4 | ✓ |
| Status machine: active → pending_pause → paused → active/retired/failed | §5 | ✓ |
| Terminal states (retired/failed) reject further transitions | §5 | ✓ |
| `retired_at` auto-set when transitioning to terminal state | §5 | ✓ |
| Single-runtime rule: one active binding per pool (when enforced) | §6 | ✓ |
| Different pools not constrained by single-runtime rule | §6 | ✓ |
| `rollback_action_type` required when `rollback_parent` is set | §7 | ✓ |
| All 3 rollback action types accepted (replace/pause_then_replace/liquidate_then_replace) | §7 | ✓ |

---

## DEP-003 Integration Compatibility

`services/deployment/service.py` `_find_runtime_binding_for_plan()` reads:
- `binding_id` → `_runtime_binding_id()` ✓
- `plan_id` → store query key ✓
- `deployment_mode` → `_runtime_binding_stage()` ✓
- `status` → `_runtime_status()` ✓
- `runtime_id` → `_runtime_id()` ✓

All fields present in the delivered schema.

---

## Non-Blocking Observation

JSON schema declares `"format": "uuid"` for `binding_id`, but the Python implementation and tests use non-UUID string IDs (`"rtb-aaa000000001"`, `"rtb-001"`). Since JSON Schema draft-07 format keywords are advisory (non-validating by default), this has no runtime impact. The annotation may be updated in a follow-up to reflect actual ID format semantics.

---

## Review Decision

APPROVED. RuntimeBinding schema is correct and complete:
- 10 required fields with proper enum validation and semantic checks
- Status machine and terminal-state guards match contract.md §5
- Single-runtime enforcement correct per §6
- Rollback lineage fields correct per §7
- File-backed store with persistence round-trip verified
- 45 pytest + 10/10 smoke all confirmed in reviewer environment
- DEP-003 integration compatibility verified

Returning to Claude (owner) for finalization.
