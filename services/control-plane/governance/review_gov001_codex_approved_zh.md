# GOV-001 Review

審查結果：通過。

本次 reviewer 在批准前補齊兩個會影響下游語意的缺口：

1. `ApprovalDecision.create_proposed()` 原先會先填入假的 `approved` outcome 與 `decided_at`，而 `decide()` 也允許直接從 `proposed` 跳到 `decided`。這會讓 `ApprovalDecision` 的 state machine 與 contract 不一致，並誤導 DEP-001 / promotion gate 的後續整合。現已修正為：
   - `proposed` 僅表示案件建立完成，沒有預先批准結果
   - 必須先 `accept_review()` 進入 `under_review`，才能 `decide()`
   - schema / contract / tests 已同步收斂

2. `revoke()` 原先只覆寫 `actor_id`，保留舊的 `actor_role`，且未檢查撤銷權限。這會污染 audit trail。現已修正為：
   - `revoke(actor_role, actor_id)` 明確要求撤銷者角色
   - 僅允許 `risk_owner` / `governance_committee`
   - audit 事件會攜帶正確的撤銷者角色與身分

另同步修正文檔中的 DeploymentPlan 整合條件，移除不存在於 `ApprovalDecision` schema 的 `target_mode` 欄位，改為對齊現有 `target_id` / `target_version` / `capital_pool_id` / `persona_id` 語意。

驗證：

- `python3 -m unittest discover -s services/control-plane/governance -p 'test_*.py'`
  - 38 tests passed
- `python3 services/control-plane/governance/smoke_test_approval_decision.py`
  - 29 smoke checks passed

結論：`ApprovalDecision` 現在已符合 GOV-001 的三個 acceptance criteria，且 state semantics / owner matrix / deployment integration 已無明顯自我矛盾，可進入 `review_approved`。
