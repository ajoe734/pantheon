# DEP-001 Review

審查結果：**通過 (APPROVED)**

## 審查範圍

審查以下 DEP-001 產出物：

- `services/control-plane/governance/deployment_plan.contract.md`
- `services/control-plane/governance/deployment_plan.schema.json`
- `services/control-plane/governance/deployment_plan.py`
- `services/control-plane/governance/test_deployment_plan.py`
- `services/control-plane/governance/smoke_test_deployment_plan.py`
- `services/control-plane/cron/service.py`（deploy workflow 整合）

驗證：
- `python3 -m unittest discover -s services/control-plane/governance -p 'test_*.py'` — 61 tests OK
- `python3 -m unittest discover -s services/control-plane/cron -p 'test_*.py'` — 9 tests OK
- `python3 services/control-plane/governance/smoke_test_deployment_plan.py` — 17/17 PASS

## 重點審查項目

### 1. Stage Transition Strictness ✅

`StagePlanner.derive_transition_type()` 正確執行所有允許與禁止的轉換：

| 轉換 | 結果 |
|---|---|
| `none → paper` | `activate` ✅ |
| `paper → canary` | `promote` ✅ |
| `canary → live` | `promote` ✅ |
| `paper/canary/live → frozen` | `freeze` ✅ |
| `frozen → paper/canary/live` | `resume` ✅ |
| `canary → paper` | `rollback` ✅ |
| `live → canary/paper` | `rollback` ✅ |
| `none → canary/live` | forbidden ✅ |
| `paper → live`（跳級） | forbidden ✅ |
| no-op（same stage） | forbidden ✅ |

### 2. Rollback Linkage for DEP-002 ✅

Rollback 為一等物件，包含：
- `target_artifact_id` / `target_version`：明確的回退目標
- `action_type`：限定為 `replace_binding`、`pause_then_replace`、`liquidate_then_replace` 三者之一
- `verified_at`：最後驗證時間戳記

規則：
- 所有指向 `paper`/`canary`/`live` 的 plan 都必須攜帶 rollback
- rollback 目標不得指向相同 artifact/version
- rollback 的 `action_type` 必須使用三個 rollback-aware action

DEP-002 的 saga compensation 將擁有足夠的資訊來執行補償邏輯。

### 3. Execution Projection for EX-001/Runtime-Manager Migration ✅

Projection 攜帶所有必要欄位：

核心欄位：
- `registry_id`, `strategy_id`, `version`, `artifact_type`
- `artifact_state`（必須為 `approved`）
- `deployment_stage`, `deployment_plan_id`, `approval_decision_id`
- `capital_pool_id`, `runtime_action`, `checksum`, `lineage`

選用欄位：
- `approved_at`, `sponsor_persona_id`
- `capital_scale_pct`, `gross_scale_pct`
- `rollback`（含 `target_registry_id`, `target_version`, `action_type`）

Legacy 相容：
- `promotion_state` 僅對 `paper`/`live` 保留，`canary`/`frozen` 不帶 — 與 contract 一致

### 4. ApprovalDecision Integration ✅

`_validate_approval_decision()` 執行 7 項檢查：
1. `decision_id` 匹配
2. `decision_state == "decided"`
3. `decision` 為 `approved` 或 `approved_with_conditions`
4. `target_id` 匹配 registry entry
5. `target_version` 匹配
6. `capital_pool_id` 匹配（如已指定）
7. `persona_id` 匹配 sponsor_persona_id（如已指定）

### 5. Scale Policy Defaults ✅

與 `PAPER_CANARY_LIVE_POLICY.md` 完全一致：

| Stage | capital_scale_pct | gross_scale_pct |
|---|---|---|
| paper | 0 | 100 |
| canary | 5 | 25 |
| live | 100 | 100 |
| frozen | 0 | 0 |

Canary 硬限制：`capital_scale_pct ≤ 5`、`gross_scale_pct ≤ 25` ✅

### 6. Cron Deploy Integration ✅

Deploy workflow 正確：
- 先建立 `DeploymentPlan`，再產生 execution projection
- 不直接呼叫 LEAN
- 每個 active-stage plan 都帶有 explicit rollback linkage
- registry entry 的 `deployment_summary` 更新為 `current_stage` + `deployment_plan_id`

## 結論

DEP-001 的所有 acceptance criteria 已滿足：
1. ✅ deployment plan 支援 paper/canary/live/frozen 轉換
2. ✅ rollback linkage 為 explicit

所有測試通過，contract 與實作一致，與上下游文件（PAPER_CANARY_LIVE_POLICY.md、BINDING_AND_DEPLOYMENT_SEMANTICS.md、ROLLBACK_AND_POSITION_SEMANTICS.md、CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md）的引用關係正確。

**批准 DEP-001 進入 `review_approved`。**
