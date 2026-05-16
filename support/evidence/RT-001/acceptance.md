# RT-001 Evidence: RuntimeBinding Schema

**Task:** RT-001 — RuntimeBinding schema  
**Phase:** Sprint 3 / EPIC-RUNTIME  
**Owner:** Claude  
**Reviewer:** Claude2  
**Date:** 2026-05-16

---

## Scope

Deliver a canonical `RuntimeBinding` platform object with:
- Correct required fields (cross-object references: `plan_id`, `persona_capital_binding_id`, `deployment_mode`)
- Valid status state machine: `active → pending_pause → paused → active/retired/failed`
- Single-runtime enforcement per capital pool
- Rollback lineage fields
- File-backed store with persistence round-trip
- JSON schema at `runtime_binding.schema.json`
- Full pytest suite at `test_runtime_binding.py`

---

## Artifacts Delivered

| File | Purpose |
|---|---|
| `services/execution/runtime-manager/runtime_binding.py` | Canonical platform object: `RuntimeBinding`, `RuntimeBindingStore`, `validate_binding()`, enums |
| `services/execution/runtime-manager/runtime_binding.schema.json` | Machine-readable JSON schema (JSON Schema draft-07) |
| `services/execution/runtime-manager/test_runtime_binding.py` | Pytest test suite (RT-001) |
| `services/execution/runtime-manager/smoke_test_runtime_binding.py` | Standalone smoke test (pre-existing) |
| `services/execution/runtime-manager/contract.md` | Authority boundary and lifecycle contract |

---

## Schema Field Coverage

| Field | Required | Description |
|---|---|---|
| `binding_id` | ✓ | Unique identifier for this binding instance |
| `runtime_id` | ✓ | LEAN instance / container / worker process |
| `capital_pool_id` | ✓ | Capital pool this binding services |
| `artifact_id` | ✓ | Approved artifact being executed |
| `artifact_version` | ✓ | Specific artifact version (semver) |
| `deployment_mode` | ✓ | Actual execution stage: paper/canary/live/frozen |
| `effective_at` | ✓ | When binding became active (UTC ISO-8601) |
| `status` | ✓ | Operational status: active/pending_pause/paused/retired/failed |
| `plan_id` | ✓ | Cross-ref to DeploymentPlan (governance provenance) |
| `persona_capital_binding_id` | ✓ | Cross-ref to PersonaCapitalBinding (governance admissibility proof) |
| `retired_at` | optional | Set when status reaches retired/failed |
| `rollback_parent` | optional | binding_id of replaced binding during rollback |
| `rollback_action_type` | optional | Required when rollback_parent is set |
| `metadata` | optional | Arbitrary execution-plane metadata |

---

## Contract Invariants Verified

| Invariant | Status |
|---|---|
| `plan_id` required — no binding without backing DeploymentPlan | ✓ |
| `persona_capital_binding_id` required — governance admissibility proof | ✓ |
| `deployment_mode` must be paper/canary/live/frozen | ✓ |
| Single-runtime rule: one active binding per pool when enforced | ✓ |
| Terminal states (retired/failed) have no further transitions | ✓ |
| `retired_at` required for terminal bindings | ✓ |
| `rollback_action_type` required when `rollback_parent` is set | ✓ |

---

## Telemetry Attribution Invariants (SD-P0-03)

| Invariant | Field | Status |
|---|---|---|
| INV-CTX-001: RuntimeBinding is canonical runtime identity | `binding_id` | ✓ |
| INV-CTX-003: telemetry must carry `binding_id` when binding exists | `binding_id` | ✓ schema field |
| INV-CTX-004: `deployment_stage` must match `deployment_mode` | `deployment_mode` | ✓ |
| INV-CTX-005: `artifact_id` must match telemetry | `artifact_id` | ✓ |
| INV-CTX-006: `capital_pool_id` must match telemetry | `capital_pool_id` | ✓ |

---

## Verification

### Smoke test (standalone)

```
python3 services/execution/runtime-manager/smoke_test_runtime_binding.py
Results: 10/10 test groups passed
ALL PASS
```

### Pytest suite (RT-001)

```
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/execution/runtime-manager/test_runtime_binding.py -q
45 passed in 5.59s
```

### py_compile check

```
python3 -m py_compile services/execution/runtime-manager/runtime_binding.py
# exit 0
```

---

## Integration Notes

- DEP-003 (`deployment_projection`) already reads RuntimeBinding from `PANTHEON_RUNTIME_BINDING_STORE_PATH` via `_find_runtime_binding_for_plan()` — compatible with `binding_id`/`plan_id`/`deployment_mode`/`status` fields
- RT-002 (Runtime Manager skeleton) uses `RuntimeBindingStore` write methods; schema changes here propagate there automatically
- RT-003 (`/bff/runtimes`) will expose bindings read via `RuntimeBindingStore.list_all()` and `find_by_pool()` — store API is stable

---

## Non-Scope

- No bridge context fields (`engine_bridge_*`, `launch_manifest_hash`) — LEAN bootstrap wiring is handled by `runtime_bootstrap.py` in `lean_runtime` (P0-CTX-002 already delivered)
- No live/canary activation — paper-only P0 sprint; fail-closed gate enforced in RT-002/RT-004
- No telemetry event emission — TEL-001 scope
