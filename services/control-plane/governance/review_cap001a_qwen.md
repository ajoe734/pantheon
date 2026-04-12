# CAP-001 Acceptance Packet & Binding Compatibility Audit

**Task:** `CAP-001A`
**Author:** Qwen
**Reviewer:** Codex
**Date:** 2026-04-10
**Status:** review

---

## 1. Scope

This packet supports `CAP-001` (Define capital_pool and PersonaCapitalBinding objects)
and contains three deliverables:

1. **Acceptance checklist** — verifies pool ownership and single-pool runtime rule
2. **Binding compatibility audit** — maps current governance files and identifies schema drift
3. **Schema drift report** — compares implementation, JSON schemas, L1 policy docs, and DB schema

---

## 2. Acceptance Checklist for CAP-001

| # | Acceptance Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Pool ownership is explicit | ✅ PASS | `CapitalPool.owner_id` + `owner_type` (org/fund/desk/operator) in `capital_pool.py` §2.3; write owner documented in contract §2.2 |
| 2 | Single-pool runtime rule is documented | ✅ PASS | `single_runtime_enforced` flag in `CapitalPool`; `CapitalPoolStore.is_single_runtime_enforced()` query helper; `PersonaCapitalBindingStore._check_single_live_owner()` enforces one live_owner per pool |
| 3 | `CapitalPool` Python implementation compiles | ✅ PASS | `py_compile` clean |
| 4 | `PersonaCapitalBinding` Python implementation compiles | ✅ PASS | `py_compile` clean |
| 5 | JSON schemas exist and are well-formed | ✅ PASS | `capital_pool.schema.json`, `persona_capital_binding.schema.json` both use Draft-07 with `additionalProperties: false` |
| 6 | Governance contract document exists | ✅ PASS | `capital_pool.contract.md` covers CapitalPool §2 and PersonaCapitalBinding §3 |
| 7 | Unit tests exist (pytest format) | ⚠️ PARTIAL | `test_capital_pool.py` (18 tests) and `test_persona_capital_binding.py` (23 tests) exist but require `pytest` which is not installed in this environment |
| 8 | `validate_pool_json()` uses jsonschema (optional dep) | ⚠️ PARTIAL | Both validation helpers silently skip if `jsonschema` not installed — acceptable for dev but should be noted |

### 2.1 Single-Pool Runtime Rule — Detailed Verification

The rule "one capital pool = one LEAN runtime at a time" is enforced at two levels:

**Level 1 — CapitalPool flag:**
- `CapitalPool.single_runtime_enforced` (default `True`) — `capital_pool.py` line ~67
- `CapitalPoolStore.is_single_runtime_enforced(pool_id)` — callers MUST check this before creating a RuntimeBinding

**Level 2 — PersonaCapitalBinding live_owner constraint:**
- `PersonaCapitalBindingStore._check_single_live_owner()` — prevents a second active `live_owner` binding for the same pool
- `PersonaCapitalBindingStore.activate()` checks this before setting status to `active`
- `PersonaCapitalBindingStore.create()` also checks it when the binding is created with `status=active`

**Verdict:** The enforcement is correct but split across two objects. Callers in RUN-001 (runtime-manager) will need to check BOTH:
1. `CapitalPoolStore.is_single_runtime_enforced(pool_id)` — does this pool allow only one runtime?
2. `PersonaCapitalBindingStore.live_owner_for_pool(pool_id)` — who is the current live sponsor?

This is correctly documented in the contract §7 (Relationship to Downstream Tasks).

---

## 3. Governance File Map

| File | Role | Relationship to CAP-001 |
|---|---|---|
| `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | L1 policy | Defines binding vs deployment semantics; `allowed_deployment_scope` semantics (§3.2, §4.1); deployment chain (§7.1) |
| `PERSONA_RUNTIME_MODEL.md` | L1 policy | Defines persona registry/session/runtime layers; binding interactions referenced |
| `services/control-plane/governance/capital_pool.contract.md` | Contract | CapitalPool and PersonaCapitalBinding governance contract (Claude's primary artifact) |
| `services/control-plane/governance/capital_pool.schema.json` | JSON Schema | Machine-readable CapitalPool schema |
| `services/control-plane/governance/capital_pool.py` | Implementation | CapitalPool dataclass + CapitalPoolStore |
| `services/control-plane/governance/persona_capital_binding.schema.json` | JSON Schema | Machine-readable PersonaCapitalBinding schema |
| `services/control-plane/governance/persona_capital_binding.py` | Implementation | PersonaCapitalBinding dataclass + PersonaCapitalBindingStore |
| `services/control-plane/governance/contract.md` | Contract | ApprovalDecision contract; references `persona_capital_binding` event type |
| `services/control-plane/governance/deployment_plan.contract.md` | Contract | DeploymentPlan contract; downstream consumer of PersonaCapitalBinding |
| `services/control-plane/governance/test_capital_pool.py` | Tests | 18 pytest tests for CapitalPool |
| `services/control-plane/governance/test_persona_capital_binding.py` | Tests | 23 pytest tests for PersonaCapitalBinding |
| `Pantheon_資料表_Schema_設計版.md` | DB Schema | §10.1 `capital.capital_pools`, §10.4 `capital.persona_capital_bindings` |

---

## 4. Schema Drift Report

### 4.1 CapitalPool — Implementation vs JSON Schema vs L1 Doc vs DB Schema

| Field | Python (`capital_pool.py`) | JSON Schema | Contract Doc (§2.3) | DB Schema (`capital_pools`) | Drift? |
|---|---|---|---|---|---|
| `pool_id` | ✅ str | ✅ str | ✅ str | ✅ `capital_pool_id` (text pk) | ⚠️ Name: `pool_id` vs `capital_pool_id` |
| `name` | ✅ str | ✅ str | ✅ str | ✅ `name` | — |
| `owner_id` | ✅ str | ✅ str | ✅ str | — | ⚠️ Missing in DB schema |
| `owner_type` | ✅ enum | ✅ enum | ✅ enum | — | ⚠️ Missing in DB schema |
| `status` | ✅ enum | ✅ enum | ✅ enum | ✅ `status` (text) | ⚠️ Values differ (see below) |
| `created_at` | ✅ str | ✅ str | ✅ str | ✅ `created_at` | — |
| `description` | ✅ opt str | ✅ str | ✅ opt | — | ⚠️ Missing in DB schema |
| `currency` | ✅ str (def "USD") | ✅ str (def "USD") | — | ⚠️ `base_currency` | ⚠️ Name: `currency` vs `base_currency` |
| `budget` | ✅ opt float | ✅ number (min:0) | — | — | ⚠️ Missing in DB schema |
| `risk_policy_ref` | ✅ opt str | ✅ str | — | ✅ `risk_policy_id` | ⚠️ Name: `risk_policy_ref` vs `risk_policy_id` |
| `single_runtime_enforced` | ✅ bool (def true) | ✅ bool (def true) | ✅ documented | — | ⚠️ Missing in DB schema |
| `updated_at` | ✅ opt str | ✅ str | — | — | — |
| `metadata` | ✅ dict | ✅ object | — | — | — |
| `desk` | — | — | — | ✅ `desk` | ⚠️ In DB but not in Python/JSON schema |
| `allowed_asset_classes_json` | — | — | — | ✅ jsonb | ⚠️ In DB but not in Python/JSON schema |
| `allowed_strategy_families_json` | — | — | — | ✅ jsonb | ⚠️ In DB but not in Python/JSON schema |
| `broker_account_ref` | — | — | — | ✅ text fk | ⚠️ In DB but not in Python/JSON schema |
| `runtime_group` | — | — | — | ✅ text | ⚠️ In DB but not in Python/JSON schema |

**Status values drift:**
- Python/JSON Schema: `active`, `suspended`, `archived`
- DB Schema: `provisioned`, `paper_bound`, `canary_bound`, `live_bound`, `risk_off`, `paused`, `liquidating`, `archived`

**Analysis:** The Python implementation and JSON schema represent a **governance-layer** view of the pool (simple lifecycle), while the DB schema represents an **execution-layer** view (detailed deployment stage tracking). This is a deliberate design split — the governance contract tracks ownership and basic lifecycle, while the runtime tracks deployment stage progression. However, this should be explicitly documented as intentional, not accidental divergence.

### 4.2 PersonaCapitalBinding — Implementation vs JSON Schema vs L1 Doc vs DB Schema

| Field | Python (`persona_capital_binding.py`) | JSON Schema | Contract Doc (§3.3) | L1 Doc (§4.1) | DB Schema (`persona_capital_bindings`) | Drift? |
|---|---|---|---|---|---|---|
| `binding_id` | ✅ str | ✅ str | ✅ str | ✅ binding_id | ✅ text pk | — |
| `persona_id` | ✅ str | ✅ str | ✅ str | ✅ persona_id | ✅ text fk | — |
| `capital_pool_id` | ✅ str | ✅ str | ✅ str | ✅ capital_pool_id | ✅ text fk | — |
| `role` | ✅ enum | ✅ enum | ✅ enum | ✅ enum | ✅ text | — |
| `allowed_deployment_scope` | ✅ enum | ✅ enum | ✅ enum | ✅ renamed from deployment_mode | ⚠️ `deployment_mode` | ⚠️ **MAJOR**: DB schema uses old name |
| `status` | ✅ enum | ✅ enum | ✅ enum | — | ⚠️ `status` (text) | ⚠️ Values differ (see below) |
| `created_at` | ✅ str | ✅ str | ✅ str | — | — | — |
| `mandate` | ✅ opt str | ✅ str | ✅ mandate | ✅ mandate | ✅ text | — |
| `budget` | ✅ opt float | ✅ number (min:0) | — | ✅ budget | ✅ numeric | — |
| `effective_from` | ✅ opt str | ✅ str | ✅ effective_from | ✅ effective_from | ✅ timestamptz | — |
| `effective_to` | ✅ opt str | ✅ str | ✅ effective_to | ✅ effective_to | ✅ timestamptz | — |
| `approval_decision_id` | ✅ opt str* | ✅ str | ✅ documented | — | — | — |
| `created_by` | ✅ opt str | ✅ str | — | — | — | — |
| `updated_at` | ✅ opt str | ✅ str | — | — | — | — |
| `metadata` | ✅ dict | ✅ object | — | — | — | — |

**Status values drift:**
- Python/JSON Schema: `pending`, `active`, `suspended`, `revoked`, `expired`
- DB Schema: `active`, `inactive`

**Analysis:** The DB schema has a much coarser status model. This could be intentional (DB stores simplified status while the Python model tracks the full lifecycle), but it creates risk: if the DB is the persistence layer, the fine-grained statuses (`pending`, `suspended`, `revoked`, `expired`) would need to be mapped to just `active`/`inactive`, losing information.

### 4.3 Critical Drift Items

| # | Item | Severity | Description |
|---|---|---|---|
| D1 | `allowed_deployment_scope` vs `deployment_mode` | **HIGH** | DB schema still uses `deployment_mode` while L1 doc, contract, Python, and JSON schema all use `allowed_deployment_scope`. The rename was documented in `BINDING_AND_DEPLOYMENT_SEMANTICS.md` §3.2 but the DB schema was not updated. |
| D2 | CapitalPool status values | **MEDIUM** | Python/JSON uses 3 states (`active/suspended/archived`); DB uses 8 states (`provisioned/paper_bound/canary_bound/live_bound/risk_off/paused/liquidating/archived`). If these are meant to be different views, the mapping must be documented. |
| D3 | PersonaCapitalBinding status values | **MEDIUM** | Python/JSON uses 5 states; DB uses 2 states (`active/inactive`). Same concern as D2. |
| D4 | Missing `owner_id`/`owner_type` in DB | **MEDIUM** | `CapitalPool.owner_id` and `owner_type` are core governance fields but absent from DB schema. If DB schema is intended for execution-layer only, this is acceptable but should be documented. |
| D5 | Missing `single_runtime_enforced` in DB | **LOW** | Governance flag absent from DB schema. Acceptable if this is a governance-only concern, but runtime-manager will need to read this value. |
| D6 | DB-only fields not in Python model | **LOW** | `desk`, `allowed_asset_classes_json`, `allowed_strategy_families_json`, `broker_account_ref`, `runtime_group` exist in DB but not in the governance Python model. These are likely execution-layer concerns. |

---

## 5. Integration Points with Downstream Tasks

| Downstream Task | Dependency | Verification |
|---|---|---|
| `RUN-001` (RuntimeBinding) | Must check `PersonaCapitalBindingStore.persona_may_deploy_to()` and `CapitalPoolStore.is_single_runtime_enforced()` | Contract §7 documents this; not yet implemented |
| `CAP-002` (multi-persona synthesis) | Requires active advisor bindings | Contract §7 documents this |
| `DEP-002` (deployment saga) | Must consider binding status for rollback scope | Contract §7 documents this |
| `EX-002` (rollback execution) | Needs binding status to determine rollback target | Not yet addressed |

---

## 6. Test Coverage Assessment

### test_capital_pool.py (18 tests)

| Category | Tests | Coverage |
|---|---|---|
| Construction | valid_pool, invalid_owner_type, invalid_status, negative_budget, zero_budget, single_runtime default, single_runtime can_be_false, to_dict roundtrip, to_dict excludes_none | ✅ Comprehensive |
| Validation | valid_pool_no_errors, empty_pool_id, empty_name | ✅ Basic |
| Store CRUD | create_and_get, create_duplicate, require_missing, list_by_owner, list_by_status | ✅ Good |
| Status transitions | update_status_valid, update_status_invalid | ✅ Good |
| Single-runtime rule | is_single_runtime_enforced, is_single_runtime_not_enforced | ✅ Good |
| Persistence | persistence roundtrip | ✅ Good |

**Missing test suggestions:**
- `update_status` from `active` to `archived` (direct transition — is it allowed? Code says yes, but test comments show confusion)
- `from_dict` with extra/unknown fields (should be silently ignored per current implementation)
- JSON schema validation when `jsonschema` is installed

### test_persona_capital_binding.py (23 tests)

| Category | Tests | Coverage |
|---|---|---|
| DeploymentScope | scope_ordering, permits, canary_permits_paper | ✅ Good |
| Construction | valid_binding, invalid_role, invalid_scope, invalid_status, negative_budget, is_active_true, permits_deployment_to_active, permits_deployment_to_inactive, to_dict_roundtrip | ✅ Comprehensive |
| Validation | valid_pending, active_without_approval, active_with_approval | ✅ Good |
| Store CRUD | create_and_get, create_duplicate, require_missing, list_by_persona, list_by_pool, persistence | ✅ Good |
| Activation | activate_sets_status, activate_without_approval, activate_invalid_transition | ✅ Good |
| Single-live-owner | one_live_owner, second_live_same_pool, second_live_diff_pool, multiple_advisors, multiple_paper_owners, live_owner_after_revoke, create_with_active_direct | ✅ Comprehensive |
| Deployment admissibility | may_deploy_within_scope, may_not_deploy_when_inactive, no_binding_returns_false | ✅ Good |

**Missing test suggestions:**
- `effective_from`/`effective_to` window enforcement (fields exist but are not checked during `permits_deployment_to`)
- `update_status` for non-activation transitions (suspend, revoke, expire)
- `live_owner_for_pool` returning None when no live owner exists
- JSON schema validation

---

## 7. Recommendations for CAP-001 Owner (Claude)

### Must Fix Before Lock
1. **Resolve D1 (HIGH):** Update DB schema field name from `deployment_mode` to `allowed_deployment_scope` in `Pantheon_資料表_Schema_設計版.md` §10.4. This is a clean rename that aligns the DB with the L1 policy and implementation.
2. **Document status value mapping:** If the 8-state DB status for CapitalPool and 2-state DB status for PersonaCapitalBinding are intentional (execution-layer simplification), add a mapping table to `BINDING_AND_DEPLOYMENT_SEMANTICS.md` or the contract doc.

### Should Fix Before Lock
3. **Add owner_id/owner_type to DB schema or document exclusion:** If `capital_pools` DB table is meant to store the governance view, these fields should be present. If it's execution-layer only, document why.
4. **Add effective_from/to window checks:** `PersonaCapitalBinding.permits_deployment_to()` does not check the validity window. A binding with `effective_to` in the past should not permit deployments.
5. **Clarify test comment confusion:** In `test_capital_pool.py`, the test `test_update_status_invalid_transition` has comments showing the author was unsure about `active -> archived` being allowed. The code DOES allow it. Either update the test comment or restrict the transition.

### Nice to Have
6. **Add `pytest` to environment or convert tests to unittest:** Current tests use `pytest` fixtures (`tmp_path`) and `pytest.raises` which cannot run without pytest.
7. **Add smoke test script:** Following the pattern of DEP-001/DEP-002, a `smoke_test_capital_pool.py` would help validate end-to-end.

---

## 8. Conclusion

The CAP-001 implementation is **structurally sound** with:
- Clear separation of governance (CapitalPool, PersonaCapitalBinding) from deployment (DeploymentPlan, RuntimeBinding)
- Proper single-pool runtime enforcement at both pool and binding levels
- Well-structured Python dataclasses with enum validation
- JSON schemas with `additionalProperties: false` for strict validation
- Comprehensive test coverage (41 tests total)

The primary action item is resolving the `deployment_mode` → `allowed_deployment_scope` rename drift in the DB schema (D1). Once resolved, this task is ready for formal review approval.
