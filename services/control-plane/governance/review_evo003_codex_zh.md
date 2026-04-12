# EVO-003 Review Packet

審查重點：

1. `EvolutionDecision` 現已成為正式 first-class governed object，而不是只存在於 L1 文檔敘述裡。新增：
   - `services/control-plane/governance/evolution_decision.py`
   - `services/control-plane/governance/evolution_decision.schema.json`
   - `services/control-plane/governance/evolution_decision.contract.md`

2. 這份 contract 補齊了三個之前缺失、會直接影響下游 EV-01 / EV-02 / incident / lineage / loop policy 的程式語意：
   - lifecycle：`proposed -> reviewed -> approved -> executed|rejected|canceled -> superseded`
   - actor role matrix：review / approve / execute 各自有明確授權矩陣
   - cooldown / observation：`executed` 後必須帶時間窗，且 store 會 enforce single-active-rule

3. `EvolutionDecision` 和既有契約已正式接起來：
   - `approval_decision_id` 從 `reviewed` 起為必填，對齊 `ApprovalDecision`
   - `linked_postmortem_id` 會透過 `IncidentStore.link_evolution_decision()` 回填 `Postmortem.linked_evolution_decision_id`
   - `services/control-plane/governance/contract.md` 已修正 EVO risk mapping example，避免 `retrain = medium` 的 drift 繼續誤導下游

4. action normalization 已收斂：
   - `freeze_paper` / `freeze_canary` / `freeze_live_strategy` 以 `action_type = "freeze"` + `target_stage` 表示
   - `retire_strategy` / `retire_alpha_template` 以 `action_type = "retire"` + `target_type` 表示
   - 這樣 BFF list/detail filter 可以維持單一欄位，且不丟失 stage/type 語意

驗證：

- `python3 -m unittest services/control-plane/governance/test_evolution_decision.py`
  - 17 tests passed
- `python3 services/control-plane/governance/smoke_test_evolution_decision.py`
  - 16 smoke checks passed
- `python3 -m unittest discover -s services/control-plane/governance -p 'test_*.py'`
  - 90 tests executed; 2 existing import errors unrelated to EVO-003:
    - `test_capital_pool.py` requires `pytest`
    - `test_persona_capital_binding.py` requires `pytest`

建議 reviewer 特別檢查：

- `risk_level` normalization 是否完全符合兩份 L1 evolution policy
- `single-active-rule` 對 executed-but-still-observing 決策的 active 判斷
- reverse-link sync 是否足夠作為 `evolution_decision.postmortem` 的 incident-side integration
