# RUN-001 Review — Codex

**Task**: Define RuntimeBinding and runtime-manager authority  
**Owner**: Claude  
**Reviewer**: Codex  
**Date**: 2026-04-10  
**Status**: review -> review_approved

---

## 1. Review Summary

**APPROVED.** `RuntimeBinding` 現在已經把 RUN-001 要鎖定的三個核心面向接齊：

- `RuntimeBinding` 作為 execution plane 的單一真相物件，必帶 `plan_id`、`persona_capital_binding_id`、`deployment_mode`
- `RuntimeBindingStore` 具備單 pool 單 active runtime 的寫入守門與終態不可再轉移的 status machine
- `BINDING_AND_DEPLOYMENT_SEMANTICS.md` §19 與 `services/execution/runtime-manager/contract.md` 已正式寫清 Runtime Manager 的 write authority

本輪 reviewer 另外做了兩個收斂，避免下游 `EX-002` / telemetry / lineage 直接吃到語意漂移：

1. 把 `DeploymentPlan.rollback.action_type` 從 `RuntimeAction` 拆成獨立的 canonical rollback vocabulary  
   `replace | pause_then_replace | liquidate_then_replace`
2. 在 Runtime Manager contract 補上 loader 現況說明  
   EX-001 仍是 `paper/live` legacy compatibility path；`canary/frozen` 目前依 canonical `artifact_state + deployment_stage` projection 對齊

---

## 2. Acceptance Criteria Verification

### AC1: RuntimeBinding carries the required cross-object references ✅

**Artifacts**:
- `services/execution/runtime-manager/runtime_binding.py`
- `services/execution/runtime-manager/runtime_binding.schema.json`

已驗證：

- `plan_id` 為必填，對應 `DeploymentPlan.plan_id`
- `persona_capital_binding_id` 為必填，對應 `PersonaCapitalBinding.binding_id`
- `deployment_mode` 明確代表實際 execution stage，而不是 governance scope
- smoke test 覆蓋缺欄位、合法 stage、rollback 欄位與 persistence round-trip

### AC2: Runtime Manager write authority is explicit and exclusive ✅

**Artifacts**:
- `services/execution/runtime-manager/contract.md`
- `BINDING_AND_DEPLOYMENT_SEMANTICS.md` §19

已驗證：

- Runtime Manager 是 `RuntimeBinding` 唯一 write owner
- `status` / `retired_at` / position lineage `current_managed_by_binding_id` 都被明確收斂到 Runtime Manager
- Governance Plane / Capital Pool Plane / BFF 均不可直接寫入 `RuntimeBinding`

### AC3: Single-runtime rule and status machine are enforceable in code ✅

**Artifact**:
- `services/execution/runtime-manager/runtime_binding.py`

已驗證：

- `RuntimeBindingStore.create()` 會在 `single_runtime_enforced=True` 時拒絕同 pool 的第二個 `active` binding
- `_ALLOWED_STATUS_TRANSITIONS` 對齊 `active -> pending_pause -> paused -> retired/failed` 路徑
- terminal states (`retired`, `failed`) 不可再轉移

### AC4: Rollback semantics no longer conflate runtime action with rollback semantics ✅

**Reviewer cleanup artifacts**:
- `services/control-plane/governance/deployment_plan.py`
- `services/control-plane/governance/deployment_plan.schema.json`
- `services/control-plane/governance/deployment_plan.contract.md`

已修正：

- `rollback.action_type` 改為 canonical rollback semantic，不再沿用 `RuntimeAction`
- `replace` 會正確映射到 runtime execution verb `replace_binding`
- legacy `replace_binding` 仍可在 Python compatibility path 中被 normalize 成 canonical `replace`

這個修正避免 `RUN-001` / `EX-002` / telemetry schema 後續各自再發明不同 vocabulary。

---

## 3. Verification

已重新驗證以下檢查：

- `python3 services/execution/runtime-manager/smoke_test_runtime_binding.py`
- `PYTHONPATH=services/control-plane/governance python3 -m unittest services/control-plane/governance/test_deployment_plan.py`
- `python3 services/control-plane/governance/smoke_test_deployment_plan.py`
- `python3 -m py_compile services/control-plane/governance/deployment_plan.py services/control-plane/governance/test_deployment_plan.py services/control-plane/governance/smoke_test_deployment_plan.py services/execution/runtime-manager/runtime_binding.py services/execution/runtime-manager/smoke_test_runtime_binding.py`

結果：

- `RuntimeBinding` smoke: `10/10` pass
- `DeploymentPlan` unit tests: `24/24` pass
- `DeploymentPlan` smoke: `18/18` pass
- `py_compile`: pass

---

## 4. Residual Note

這輪沒有留下 blocker。唯一仍屬 follow-on 的是 EX-001 loader migration：

- `paper/live` 仍走 legacy `promotion_state` compatibility
- `canary/frozen` 需要在 execution loader 完整吃 `artifact_state + deployment_stage` 後，才算 execution path 全面對齊

這不阻擋 RUN-001 本身通過，因為本 task 的物件語意、寫權限與 rollback vocabulary 已經鎖定。
