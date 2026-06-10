# DEP-004 Governance Review — Claude2

Task: `DEP-004`
Reviewer: Claude2 (governance-review dispatch)
Date: 2026-05-18
Verdict: **通過 / Approved**

---

## 概況

DEP-004 在 DeploymentPlan 進入 RuntimeBinding 前增加 capital_pool × runtime 相容性檢查。本次審查確認三項主要 artifact 均已落地，且所有 9 項 acceptance criteria 通過。

---

## Artifact 清單確認

| Artifact | 狀態 |
|---|---|
| `services/control-plane/governance/pool_runtime_compat.py` | ✅ 存在 |
| `services/control-plane/governance/test_pool_runtime_compat.py` | ✅ 存在 |
| `services/control-plane/governance/pool_runtime_compat_contract.md` | ✅ 存在 |
| 1-line hook（`services/control-plane/cron/service.py`）| ✅ 存在 |

---

## Acceptance Criteria 逐項驗證

1. **`check_compatibility(capital_pool_id, deployment_plan_id)` 回傳 `CompatibilityResult` dict**
   - ✅ `pool_runtime_compat.py:39` 定義符合，含 `passed: bool` 與 `rejection_reasons: list[str]`

2. **Pool admissibility status == active**
   - ✅ `pool_runtime_compat.py:93` 檢查 `pool_status != ACTIVE_STATUS` → `pool_admissibility_status_not_active`

3. **Pool risk_budget 涵蓋 DeploymentPlan target_size**
   - ✅ `pool_runtime_compat.py:101` 比較 `target_size > risk_budget` → `pool_risk_budget_insufficient`

4. **Pool jurisdiction 符合 runtime broker jurisdiction**
   - ✅ `pool_runtime_compat.py:126` 比對 `broker_jurisdiction not in pool_jurisdictions` → `pool_runtime_jurisdiction_mismatch`

5. **Runtime mode 符合 deployment_stage (paper/canary/live/frozen)**
   - ✅ `pool_runtime_compat.py:117` 比對 `runtime_mode != stage` → `runtime_mode_stage_mismatch`

6. **PersonaCapitalBinding 存在且 status == active**
   - ✅ `pool_runtime_compat.py:139–144` 檢查 binding 存在且 active → `persona_capital_binding_missing` / `persona_capital_binding_not_active`

7. **DeploymentPlan service 在 CompatibilityResult.passed == false 時拒絕 advance**
   - ✅ `services/control-plane/cron/service.py:28` `from pool_runtime_compat import enforce_compatibility`
   - ✅ `cron/service.py:270` `enforce_compatibility(...)` 在 advance path 呼叫，失敗時拋出 ValueError 中止流程

8. **Test 涵蓋 1 pass + 5 fail scenarios（each check）with pytest -q exit 0**
   - ✅ 執行 `pytest -v services/control-plane/governance/test_pool_runtime_compat.py`: **7 passed in 0.58s**
   - 1 pass scenario: `test_check_compatibility_passes_with_active_pool_matching_runtime_and_binding`
   - 5 fail scenarios (parametrized):
     - `overrides0`: `pool_admissibility_status_not_active`
     - `overrides1`: `pool_risk_budget_insufficient`
     - `overrides2`: `pool_runtime_jurisdiction_mismatch`
     - `overrides3`: `runtime_mode_stage_mismatch`
     - `overrides4`: `persona_capital_binding_not_active`
   - 1 enforce test: `test_enforce_compatibility_raises_with_rejection_reason`

9. **No live broker side effects**
   - ✅ `pool_runtime_compat.py` 全部為 read-only；無 store write、無 broker call
   - ✅ Contract Non-Goals 明確排除寫 DeploymentPlan / RuntimeBinding / CapitalPool

---

## 代碼品質觀察

- `check_compatibility` 使用 keyword-only 參數可安全組合多種 store 注入模式，不強制依賴具體 ORM 或 dataclass
- `_field` / `_store_get` 等 helper 正確處理 Mapping 與 dataclass 雙形式，可與 paper_* smoke test 物件相容
- `enforce_compatibility` wrapper 提供 fail-closed 路徑，符合 PAPER_CANARY_LIVE_POLICY 要求
- 無 import 副作用；可作為 isolated module 在任意服務層被引入

---

## 結論

DEP-004 實作完整，所有 acceptance criteria 均已滿足。建議批准推進至 `review_approved`。

LLM-Agent: Claude2
Task-ID: DEP-004
Reviewer-claim: helper governance review dispatch (2026-05-18)
