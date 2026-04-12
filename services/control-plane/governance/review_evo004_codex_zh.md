# EVO-004 Review Packet

審查範圍：

1. `services/control-plane/governance/evolution_controller.py`
   - 新增可執行的 normal-path routing layer，將 approved `EvolutionDecision` 轉為：
     - primary `DispatchCommand`
     - optional deployment `freeze_stage` follow-through
     - optional `RollbackCommand`
   - 補上 `ThresholdEvaluator`，把 L1 threshold family 映射到預設 action path
   - 補上 `create_redeploy_followthrough()`，明確表達 redeploy 不是新的 `EvolutionDecision.action_type`

2. `EVOLUTION_REVIEW_AND_THRESHOLDS.md`
   - 修正 freeze routing matrix 的一個 drift：
     - 之前把「任何沒有 active runtime 的 freeze」都落在中風險鏈上
     - 現在明確拆成：
       - `freeze` on `paper/canary` = medium-risk
       - `freeze` on `live` with no active runtime = high-risk governance-only
       - `freeze` on `live` with active runtime = high-risk + optional deployment/runtime follow-through
   - 這樣才和 `EvolutionDecision` 既有 `freeze_live -> high risk` normalization 一致

3. `services/control-plane/governance/evolution_controller_contract.md`
   - 跟著 canonical matrix 同步拆分 `freeze live no active runtime`
   - 新增 worked incident handoff
   - 新增 implementation artifact 清單

4. 驗證檔案
   - `services/control-plane/governance/test_evolution_controller.py`
   - `services/control-plane/governance/smoke_test_evolution_controller.py`

重點語意確認：

1. `freeze` 與 `rollback` 沒有再被重新混成一個物件
   - `freeze` 仍是 governance decision
   - runtime mitigation 仍透過 companion `RollbackCommand` 交給 `Rollback Controller -> Runtime Manager`

2. `freeze live` 的 owner / risk / runtime side effect 已正式拆開
   - 有無 active runtime 只影響有沒有 operational follow-through
   - 不改變 `freeze_live` 本身的 high-risk owner chain

3. redeploy 沒有形成 shadow runtime command surface
   - `create_redeploy_followthrough()` 只產生 deployment-plane command
   - metadata 明確要求新的 `ApprovalDecision` 與後續 `DeploymentPlan`
   - inherited cooldown / observation 仍沿用 parent decision，不另開新的 evolution window

4. `executed` 的 plane boundary 已可機器驗證
   - governance freeze 的 primary plane = `governance`
   - retrain/revalidate = `research`
   - force-risk-off = `runtime`
   - redeploy follow-through = `deployment`

驗證：

- `python3 -m unittest services/control-plane/governance/test_evolution_controller.py`
  - 10 tests passed
- `python3 services/control-plane/governance/smoke_test_evolution_controller.py`
  - 14 smoke checks passed
- `python3 -m unittest services/control-plane/governance/test_evolution_decision.py`
  - 17 tests passed
- `python3 services/control-plane/governance/smoke_test_evolution_decision.py`
  - 16 smoke checks passed

建議 reviewer 特別檢查：

1. `freeze live with no active runtime` 改成 high-risk governance-only，是否完全符合你對 L1 freeze semantics 的解讀。
2. `force_risk_off` 以 runtime primary plane + mandatory rollback companion 表示，是否足夠貼合 `EVO-005` 之後要做的 fast-path exception。
3. `create_redeploy_followthrough()` 要求 parent 已進 observation window，這條 gating 是否和 deployment/operator flow 預期一致。
